"""Private storyboard drafts, versioned independently of chat and render jobs."""
import hashlib
import json
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import AfterValidator, BaseModel, Field, StringConstraints, model_validator

from .images import validate_images
from .store import Store

router = APIRouter(prefix="/api/video-studio")
EMPTY_REVISION = "0" * 64
BoardKey = Annotated[str, Path(pattern=r"^(?:standalone|[a-f0-9]{32})$")]
ItemId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]


def validate_reference(value: str) -> str:
    validate_images([value])
    return value


ReferenceImage = Annotated[str, StringConstraints(max_length=175_000), AfterValidator(validate_reference)]


class Character(BaseModel):
    id: ItemId
    name: str = Field(max_length=80)
    appearance: str = Field(default="", max_length=800)
    wardrobe: str = Field(default="", max_length=800)
    reference: ReferenceImage | None = None


class Scene(BaseModel):
    id: ItemId
    title: str = Field(max_length=100)
    direction: str = Field(default="", max_length=3500)
    duration: int = Field(default=5, ge=1, le=30)
    frame: ReferenceImage | None = None
    cast: list[ItemId] = Field(default_factory=list, max_length=6)


class Storyboard(BaseModel):
    title: str = Field(default="", max_length=100)
    ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    characters: list[Character] = Field(default_factory=list, max_length=6)
    scenes: list[Scene] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def references_exist(self):
        ids = {character.id for character in self.characters}
        if len(ids) != len(self.characters) or len({scene.id for scene in self.scenes}) != len(self.scenes):
            raise ValueError("Storyboard entries must have unique IDs.")
        if any(len(set(scene.cast)) != len(scene.cast) or not ids.issuperset(scene.cast) for scene in self.scenes):
            raise ValueError("Each scene cast must reference characters in this storyboard.")
        return self


class SaveStoryboard(BaseModel):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    storyboard: Storyboard


@router.get("/{value}")
def read_storyboard(value: BoardKey):
    saved = Store().get_json("video-storyboard", value)
    return saved or {"revision": EMPTY_REVISION, "storyboard": Storyboard().model_dump()}


@router.put("/{value}")
def save_storyboard(value: BoardKey, body: SaveStoryboard):
    store = Store()
    encoded = body.storyboard.model_dump_json()
    revision = hashlib.sha256(encoded.encode()).hexdigest()
    if len(encoded.encode()) > 3_300_000:
        raise HTTPException(413, "The storyboard is too large. Remove a reference and save again.")
    result = {"revision": revision, "storyboard": body.storyboard.model_dump()}
    updated = store.call("EVAL", """
local stored = redis.call('GET',KEYS[1])
local current = ARGV[1]
if stored then current = cjson.decode(stored).revision end
if current == ARGV[3] then return 1 end
if current ~= ARGV[2] then return 0 end
redis.call('SET',KEYS[1],ARGV[4])
return 1
""", 1, store.key("video-storyboard", value), EMPTY_REVISION, body.revision, revision,
                         json.dumps(result, ensure_ascii=False))
    if not updated:
        raise HTTPException(409, "A newer storyboard was saved. Reload it before making more changes.")
    return result
