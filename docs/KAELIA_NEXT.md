# Kaelia — punkt wznowienia

Aktualizacja 17 listopada 2026: użytkownik wznowił pracę. Ukończono etap natywnych narzędzi dla Astry/Sol oraz ochrony checkpointów.

## Stan zweryfikowany

- 437 testów Python offline przeszły, wraz z testami rzeczywistych struktur SDK, checkpointów przed/po zapisie, limitu czasu i operacji, migracji starego stanu oraz prywatności odpowiedzi API.
- Kompilacja frontendu przeszła. Nie wykonano płatnej próby modelu; zgodność z rzeczywistą odpowiedzią dostawcy pozostaje osobną walidacją.
- resume_checkpoint nie jest zwracany przez publiczny odczyt zadania ani historię. Recovery pobiera go wewnętrznie.
- Potwierdzone odrzucenia żądania przez OpenAI zwalniają rezerwację; timeout i błędy serwera zachowują nieznany koszt i blokują automatyczne powtórzenie.
- Pauza przed wykonaniem operacji zachowuje pending_action; checkpoint wskazuje model protokołu. Próba użycia stanu innego modelu jest odrzucana przed wywołaniem.
- Marka Kaelia pozostaje na agents.synviocore.xyz. Starsze adaptery i Codex CLI zachowują własne ścieżki.

## Ochrona przed pętlami — wykonawca web/app/source

- Liczniki walidacji i powtórzonych operacji są zapisywane w checkpointach i przechodzą przez Resume.
- Trzeci identyczny problem przekazuje modelowi polecenie zmiany podejścia, bez zmiany modelu i uprawnień. Szósta próba tego samego problemu powoduje twardą blokadę Resume na serwerze.
- Zachowano wcześniejsze konserwatywne pauzy: dwie nieudane walidacje w jednym uruchomieniu oraz pauza po dalszym powtarzaniu tej samej operacji mimo wskazówki.
- Niepowiązane zmiany plików nie resetują liczników odczytu; odtworzenie oczekującej operacji nie liczy się jako kolejna próba.
- UI pokazuje zdarzenie zmiany podejścia i prosi o sprawdzenie problemu oraz zmienioną instrukcję po twardym zatrzymaniu. Zapisane pliki pozostają dostępne.
- Ten mechanizm nie obejmuje wewnętrznej pętli Codex CLI. Nowe, jawnie zlecone zadanie jest nowym zakresem pracy; nie jest automatycznym wznowieniem.

## Live — pierwszy rzeczywisty panel

- LivePanel używa istniejącego strumienia zdarzeń watchTask, Reacta i ikon Lucide. Bez nowej zależności i bez symulowanych działań.
- Filtry Files / Commands / Browser / Checks, maksymalnie 200 widocznych wpisów, wyniki terminala rozwijane na żądanie, przejście do wybranego pliku w istniejącym edytorze.
- Codex JSONL przekazuje rozpoczęcie i wynik polecenia oraz bezpieczne ścieżki rzeczywistych zmian plików; sekrety środowiska są usuwane z nowych wpisów.
- Zapisane screenshoty wykorzystują dotychczasowy CodexBrowser. Widok wyraźnie odróżnia screenshot od transmisji wideo. Odczyt nie tworzy środowiska; odpytywanie w tle i po zakończeniu zadania ograniczone.
- Natywne details i animacje CSS z reduced-motion. Zmiana zakładki chroni niezapisane zmiany edytora.
- 14 testów Node przeszło; kompilacja frontendu również. Testy backendu nadal bez płatnych wywołań i rzeczywistego uruchomienia Codex VM.

## Coding environment — wersja 1

- Oficjalny starter wygenerowany przez shadcn CLI 4.23.0: React 19.3.1, Vite 8.3.8, Tailwind 4.3.5, komponenty button/card/badge/separator, Radix/Lucide. Wersje i lockfile przypięte; MIT z atrybucją zachowane.
- Starter kompiluje się lokalnie. Starter buduje pojedynczy moduł do obecnego podglądu bez zewnętrznych importów. Pliki artefaktów mogą mieć do 512 KiB przy niezmienionym limicie całości 2 MiB; pliki źródłowe nadal mają limit 256 KiB. Nie jest dodawany do istniejącego repo ani do jawnie zleconego czystego HTML.
- /api/codex/environment oraz /prepare udostępniają 24-godzinny cache narzędzi. Obraz pochodzi z przygotowanego Playwright Chromium; instaluje przypięty Codex i zależności startera, wykonuje build oraz kontrolny render desktop/mobile bez modelu. Brak publicznych portów i kluczy modelu w środowisku.
- Nowe zadania Codex korzystają z gotowego CLI; nowe projekty dostają starter i cache node_modules. Dla frontendów dostępny Playwright oraz helper do screenshotów, konsoli i overflow. Istniejące stosy pozostają zachowane.
- Kontrola wizualna nie jest dowodem jakości estetycznej ani testów interakcji. Agent otrzymuje instrukcję oglądania obu screenshotów, poprawiania błędów i testowania wymaganych interakcji; browser_verified nie jest automatycznie ustawiane na true.
- Live potrafi pokazać desktop/mobile. Identyczne screenshoty nie produkują kolejnych fikcyjnych zdarzeń. Kontekst Codexa kompaktowany przy 64k zamiast 900k tokenów.
- Testy SDK/API są offline; rzeczywiste przygotowanie obrazu wymaga osobnego potwierdzenia wyniku po wdrożeniu. Nie uruchomiono modelu.

## Managed runtime v2 — bez instalacji przez użytkownika

- Zwykły interfejs nie zawiera przycisku przygotowania. Codex zapewnia środowisko automatycznie, zanim rozpocznie pracę modelu; środowisko narzędzi nie wygasa codziennie.
- Wspólny obraz jest wersjonowany i zapisywany bez terminu wygaśnięcia. Aktywne maszyny zadań pozostają krótkotrwałe. Retencja snapshotu może mieć koszt magazynu u dostawcy; nie jest obietnicą zerowej opłaty.
- Starter jest dostarczany jako jeden jawny zasób starter_catalog.json, aby tracing wdrożenia nie pomijał zagnieżdżonych package.json/package-lock.json. Katalog zawiera pełną licencję oraz dokładny lockfile.
- Preflight zasobów odbywa się przed przydziałem VM. Dzienny limit przygotowań wynosi 3, a zadania mają dotychczasowe limity kosztu i czasu. Nie stosujemy automatycznej pętli nieudanych instalacji.
- Dwie pierwsze rzeczywiste próby przygotowania obrazu zatrzymały się na npm ci. Nie oznaczono obrazu jako gotowego. Komunikaty instalatora są teraz ograniczone i redagowane z zachowaniem początku oraz końca błędu. Wynik rzeczywistego przygotowania v2 wymaga potwierdzenia po wdrożeniu.

## Dalej

1. Zweryfikować produkcyjne wdrożenie i zwykłe odczyty API bez płatnego wywołania. Dla przyszłego testu prawdziwego modelu osobno określić zakres i limit.
2. Zastąpić pozostałe tekstowe akcje w rozmowie/adaptacji dostawców natywnymi narzędziami tam, gdzie są zgodne; obecny etap obejmuje wykonawcę plików dla gpt-6-astra i gpt-6.1-sol.
3. Rozszerzyć spójny system zdarzeń i ograniczeń na adapter Codex, a następnie dodać rzeczywisty Live (plik/terminal/przeglądarka), bez animowania fikcyjnych działań.
4. Rozdzielenie danych workspace, Postgres jako źródło prawdy, rejestr modeli, pełniejsze Codex/MCP i rzeczywisty Live.
5. Potem księga portfela/płatności i studio. Najnowsze bonusy 1–10%; nie uruchamiać płatności bez księgi oraz weryfikacji zasad sprzedaży.

Zachować React/FastAPI i źródła OpenJarvis. Pełna architektura znajduje się w KAELIA_ARCHITECTURE.md w katalogu nadrzędnym sesji.


## Potwierdzenie produkcyjne — 2026-11-17

- Wdrożenie e767626: rzeczywiste przygotowanie managed runtime v2 zakończyło się sukcesem. Pełny etap obejmuje instalację Codex CLI i zależności startera, build oraz render Playwright desktop/mobile przed zapisaniem obrazu.
- Po ponownym załadowaniu produkcyjnego interfejsu panel pokazuje Build workspace / Ready, bez przycisku instalacji. Obraz narzędzi pozostaje zapisany do kolejnych zadań.
- 437 testów Python, 14 testów Node oraz kompilacja interfejsu przeszły. Nie wykonano inferencji modelu; przygotowanie korzystało z cloud compute.
- Pełne zadanie Codex z generowaniem i oceną projektu pozostaje osobnym testem. Gotowy toolchain nie stanowi gwarancji jakości projektu ani dowodu testów interakcji.
