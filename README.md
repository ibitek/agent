# SYNVIO Agents

SYNVIO Core z osobnymi trybami **Rozmowa** i **Zadanie**, przygotowany do wdrożenia na Vercelu pod `agents.synviocore.xyz`.

- **Rozmowa** jest domyślna. Zwykłe powitania, np. „siema”, dostają lokalną odpowiedź za **0 USD**. Pozostałe wiadomości wykonują jedno generowanie, bez narzędzi, testów i dostępu do plików.
- **Zadanie** używa dotychczasowego `DeveloperAgent` i OpenJarvis. Kod i testy użytkownika uruchamiają się wyłącznie w osobnych środowiskach Vercel Sandbox — bez klucza OpenAI, dostępu do lokalnego komputera i sieci wychodzącej.
- **Logowanie** chroni całą aplikację i API. To wersja dla jednego właściciela, nie otwarty serwis wieloużytkownikowy.
- **Historia, zdarzenia i pliki** zapisują się w Redis, a nie w nietrwałym dysku funkcji. Projekty można pobrać jako ZIP.
- **Koszty:** domyślny model to `gpt-4o-mini`. `gpt-4o` jest wyłączony do czasu jawnej zgody w ustawieniach. Domyślny dzienny limit obejmuje łącznie 10 wiadomości wymagających modelu i rozpoczętych zadań.

## Wdrożenie na Vercelu

### 1. Import repozytorium

W Vercelu wybierz **Add New → Project → Import Git Repository → ibitek/agent**.

Ustaw **Root Directory na katalog główny repozytorium (`.`)**. Nie wybieraj `web` — backend jest w `api/`. Plik `vercel.json` zawiera polecenia instalacji, budowania i przekierowania API.

Jeśli panel wymaga ustawień ręcznych:

| Ustawienie | Wartość |
| --- | --- |
| Framework Preset | Other |
| Install Command | `npm --prefix web ci` |
| Build Command | `npm run build` |
| Output Directory | `web/dist` |
| Node.js | 24.x |
| Python | 3.12 |

### 2. Hasło i klucz — tylko w ustawieniach Vercela

W **Settings → Environment Variables** dodaj dla środowiska Production:

| Nazwa | Wartość |
| --- | --- |
| `SYNVIO_PASSWORD` | Nowe, unikalne losowe hasło, co najmniej 20 znaków |
| `SYNVIO_SESSION_SECRET` | Inny losowy sekret, co najmniej 32 znaki |
| `OPENAI_API_KEY` | Twój klucz OpenAI |
| `SYNVIO_ENABLE_TASKS` | `1`, jeśli chcesz uruchamiać zadania i przygotować środowisko testów |
| `SYNVIO_DAILY_REQUEST_LIMIT` | `10` lub mniejszy limit na początek |
| `SYNVIO_ALLOW_EXPENSIVE_MODELS` | `0` |

Nie umieszczaj wartości w kodzie, repozytorium ani w czacie. Hasło do SYNVIO i klucz OpenAI to **dwie różne rzeczy**. Aplikacja bez ustawionego hasła i sekretu nie udostępni prywatnego API.

Nie ustawiaj `SYNVIO_CLOUD_DEV=1` na Vercelu. Ten przełącznik służy wyłącznie lokalnemu testowaniu przez HTTP.

### 3. Trwały zapis

W **Storage / Marketplace** podłącz **Upstash Redis**, wybierając darmowy plan, jeżeli jest dostępny dla Twojego konta. Podłącz bazę do tego projektu.

Backend obsługuje obie pary nazw zmiennych:

- `UPSTASH_REDIS_REST_URL` i `UPSTASH_REDIS_REST_TOKEN`, lub
- `KV_REST_API_URL` i `KV_REST_API_TOKEN`.

Bez tego magazynu działają logowanie i lokalne powitania; płatne rozmowy oraz zadania są blokowane, aby nie omijać limitów i nie gubić danych.

Po zmianie zmiennych wykonaj **Redeploy**.

### 4. Przygotowanie wykonywania testów

Otwórz adres `*.vercel.app`, zaloguj się ustawionym hasłem i wybierz **Zadanie → Przygotuj środowisko testów**.

Przycisk przygotowuje zaufany obraz z Pythonem i pytest, zapisuje jego identyfikator w Redis i zatrzymuje maszynę przygotowującą. Nie uruchamia modelu AI. Operacja zużywa limit Vercel Sandbox zgodnie z planem konta.

SDK korzysta z tożsamości OIDC Vercela. Nie dodawaj klucza OpenAI do obrazu, zmiennych środowiska Sandbox ani plików projektu. Przy każdym uruchomieniu testów powstaje świeża maszyna z wyłączoną siecią i bez sekretów.

Zamiast przygotowania przez interfejs można ustawić `SYNVIO_SANDBOX_SNAPSHOT` na identyfikator własnego, zgodnego obrazu. Musi zawierać pytest i interpreter `/vercel/sandbox/.venv/bin/python`.

### 5. Domena

Po sprawdzeniu `*.vercel.app` dodaj w **Settings → Domains** adres **`agents.synviocore.xyz`**. W panelu DNS Spaceship skopiuj **dokładnie rekord wskazany przez Vercel** dla tej subdomeny.

Nie zmieniaj serwerów nazw całej domeny i nie usuwaj rekordów istniejącej strony lub poczty. Samo przygotowanie tego repozytorium nie zmienia DNS.

## Co jest zachowane z wersji lokalnej

Pełny kod lokalnego Core pozostaje w `src/synvio/`, wraz z testami, CLI i obsługą dotychczasowych lokalnych projektów. Backend publiczny to osobny adapter `src/synvio/cloud/`, a jego wejście Vercela to `api/index.py`.

OpenJarvis jest zależnością przypiętą do sprawdzonego commitu `68d4290663984508db94a6a59ade45c2b17859ed`. Jego źródła nie są modyfikowane ani kopiowane do tego repozytorium. Dodatkowe informacje o lokalnym Core: [docs/LOCAL_CORE.md](docs/LOCAL_CORE.md).

## Uruchomienie lokalne

Wymagane Python 3.12 i Node.js 24:

```text
python -m venv .venv
```

Aktywuj `.venv` (`.venv\Scripts\Activate.ps1` w PowerShell lub `source .venv/bin/activate` w Bash), a następnie:

```text
python -m pip install -r requirements.txt
python -m pip install -e ".[dev]"
npm --prefix web ci
```

- Lokalny Core: `python -m synvio.web_cli`, następnie `npm run dev`.
- Adapter chmurowy do testowania lokalnie: `python -m uvicorn synvio.cloud.app:app --host 127.0.0.1 --port 8000`, następnie `npm run dev`. Do lokalnego HTTP potrzebne jest `SYNVIO_CLOUD_DEV=1` i własne testowe hasło/sekret w środowisku procesu. Nie zapisuj ich w Git.

## Weryfikacja

```text
python tools/verify_offline.py
npm run test:ui
npm run build
```

`verify_offline.py` usuwa rzeczywiste klucze ze środowiska, blokuje połączenia zewnętrzne i używa symulowanych modeli, magazynu Redis oraz maszyn Sandbox. Istniejące testy Core rzeczywiście wykonują pytest w tymczasowych projektach.

**Te kontrole nie są testem produkcyjnego Vercela, prawdziwej usługi Redis ani prawdziwego Vercel Sandbox.** Dopiero wdrożenie i podłączenie konta pozwolą sprawdzić te integracje. Nie wykonano nowej płatnej próby AI podczas przygotowania repozytorium.

## Granice i limity

- Jeden aktywny programistyczny job na projekt. Atomowe przejęcie joba blokuje podwójne generowanie po ponowieniu żądania.
- Zadanie uruchamia się w aktywnym żądaniu `/run`, nie w porzuconym wątku po zakończeniu funkcji. Proces ma limit 220 sekund; funkcja Vercela — 300 sekund. Przerwane zadanie nie jest automatycznie ponawiane i nie wraca do stanu sukcesu.
- 10 kroków, 8 wywołań narzędzi, 1 próba, maksymalnie 2 testy zlecone przez model oraz test kontrolny Core. Każdy test ma limit 30 sekund.
- Chmurowy runner obsługuje Python i stałe polecenia pytest/unittest. Nie wykonuje modelowych poleceń shell, nie instaluje dowolnych zależności i nie używa hosta jako awaryjnego backendu.
- Projekt: maksymalnie 100 plików tekstowych, 256 KiB na plik i 2 MiB łącznie. Logi Core, ukryte pliki i typowe pliki kluczy nie są przekazywane do środowiska testów.
- Historia zadań jest przechowywana 30 dni. Aktualne pliki projektu pozostają w Redis do usunięcia danych lub bazy przez właściciela.
- Dzienny limit wiadomości/zadań nie jest gwarancją maksymalnej kwoty w USD. Model, Vercel, magazyn danych i domena mają oddzielne zasady rozliczeń. Darmowy Vercel Hobby ma limity i jest przeznaczony do użytku osobistego, niekomercyjnego.
- Nie przechowuj repozytoriów produkcyjnych ani sekretów w projekcie testowym. To początkowe wdrożenie dla jednego właściciela, bez funkcji współdzielenia kont.

Dokumentacja dostawcy: [Python](https://vercel.com/docs/functions/runtimes/python), [Sandbox](https://vercel.com/docs/sandbox), [limity Hobby](https://vercel.com/docs/plans/hobby).
