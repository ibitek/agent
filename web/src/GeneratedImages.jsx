import { Download, Pencil } from 'lucide-react'
import { generatedImageUrl } from './imageAssets.js'

export default function GeneratedImages({ images, onEdit, disabled }) {
  return <div className="generated-images" aria-label="Generated images">
    {images.map((image, index) => <figure key={image.id}>
      <img src={generatedImageUrl(image.id)} loading="lazy" alt={'Generated image ' + (index + 1)} />
      <figcaption>
        <button type="button" disabled={disabled} onClick={() => onEdit(image)}><Pencil size={13} />Edit in Studio</button>
        <a href={generatedImageUrl(image.id)} download={image.name} aria-label={'Download generated image ' + (index + 1)}>
          <Download size={14} />
        </a>
      </figcaption>
    </figure>)}
  </div>
}
