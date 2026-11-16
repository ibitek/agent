# Kaelia — punkt wznowienia

Przerwano na prośbę użytkownika 17 listopada 2026. Nie uruchamiać płatnych testów ani wdrożenia automatycznie po odczytaniu tego pliku; obowiązuje aktualne polecenie użytkownika.

## Produkcja

Commit `7835113`: marka Kaelia wdrożona na https://agents.synviocore.xyz. Nowa domena będzie kupiona później. Sesja, historia oraz podgląd zweryfikowane w przeglądarce. Przed wdrożeniem przeszło 375 testów Python, 9 testów Node i kompilacja frontendu.

## Zapisany etap roboczy — jeszcze niewdrożony

- `native_tools.py`: stały, ścisły schemat workspace_action; lokalna walidacja, jedna akcja lub atomowy batch, powiązanie call_id z wynikiem, zachowanie ostatniej natywnej tury wraz z zaszyfrowanym stanem reasoning.
- `responses.py`: natywne narzędzia dla gpt-6-astra / gpt-6.1-sol, store=false, brak równoległych wywołań i brak tekstowego JSON mode w tej ścieżce.
- `metering.py`: koszt schematu i natywnej historii uwzględniony w rezerwacji; zapis identyfikatora odpowiedzi.
- `web_builder.py`: użycie natywnych narzędzi tylko przez obsługujący je adapter; checkpoint wersji 2; istniejąca walidacja plików i starsze adaptery zachowane. Niepełna/niepoprawna odpowiedź powoduje pauzę bez automatycznego powtórzenia płatnego wywołania.
- `test_native_tools.py`: testy offline błędów, powiązania wyników z wywołaniami, wznowienia oczekującej poprawki i ochrony budżetu.

Ostatnia walidacja tego etapu: **35 testów przeszło** (native_tools, source_syntax, responses_engine, metering, web_builder). Testy używają atrap; nie wykonano płatnego wywołania, pełnej regresji ani wdrożenia tej zmiany. Składnia i testy kontraktów nie dowodzą jeszcze zgodności na rzeczywistym API.

## Następne kroki, w kolejności

1. Przejrzeć integrację natywnego protokołu i testy. Poprawić test wire-format, aby rzeczywiście serializował niepusty output SDK; obecnie sprawdza głównie parametry żądania.
2. Usunąć resume_checkpoint z publicznych odpowiedzi task_row (obecnie filtr usuwa tylko images/codex_proxy_digest). Recovery powinno odczytywać ten wewnętrzny stan bezpośrednio z magazynu. Dodać test, że stan modelu i pełne argumenty nie trafiają do publicznych danych zadania/Activity.
3. Sprawdzić awarię przed zapisem i po zapisie checkpointu, granice limitu operacji, wznowienie odrzuconej akcji oraz stary checkpoint JSON przechodzący na natywny adapter. Sprawdzić odtworzenie pól modelu i wersji protokołu.
4. Rozróżnić jednoznacznie odrzucone żądania API (np. 400, 401, 429) od nieznanego zużycia po przerwanym połączeniu. Nie zatrzymywać niepotrzebnie rezerwacji przy potwierdzonym odrzuceniu; nigdy nie ponawiać płatnych operacji w ciemno. Testy bez sieci.
5. Uruchomić pełną regresję offline. W tym środowisku TestClient zawieszał się przy uruchomieniu ograniczonym; ten sam verify_offline.py po eskalacji działał. Skrypt usuwa klucze, blokuje zewnętrzną sieć i izoluje katalog SDK.
6. Dopiero po pozytywnych wynikach wysłać zmiany i zweryfikować wdrożenie. Rzeczywisty test modelu jest osobnym etapem z ustalonym budżetem; podczas tej sesji go nie wykonywano.
7. Następnie: trwały stan zadań i liczniki anti-loop przez wznowienia, rozdzielenie danych workspace, rejestr modeli, rozszerzenie integracji Codex/MCP, prawdziwy Live i pomiar jakości wizualnej.

Architektura docelowa: dokument KAELIA_ARCHITECTURE.md w katalogu nadrzędnym sesji. Zachować React/FastAPI, nie modyfikować OpenJarvis. Najnowszy zakres bonusów 1–10%; księga i płatności dopiero po przygotowaniu izolacji oraz rozliczeń. Zmiana marki nie jest ukończeniem przebudowy wykonawcy.
