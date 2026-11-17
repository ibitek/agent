# Kaelia — punkt wznowienia

Aktualizacja 17 listopada 2026: użytkownik wznowił pracę. Ukończono etap natywnych narzędzi dla Astry/Sol oraz ochrony checkpointów.

## Stan zweryfikowany

- 403 testy Python offline przeszły, wraz z testami rzeczywistych struktur SDK, checkpointów przed/po zapisie, limitu czasu i operacji, migracji starego stanu oraz prywatności odpowiedzi API.
- Kompilacja frontendu przeszła. Nie wykonano płatnej próby modelu; zgodność z rzeczywistą odpowiedzią dostawcy pozostaje osobną walidacją.
- resume_checkpoint nie jest zwracany przez publiczny odczyt zadania ani historię. Recovery pobiera go wewnętrznie.
- Potwierdzone odrzucenia żądania przez OpenAI zwalniają rezerwację; timeout i błędy serwera zachowują nieznany koszt i blokują automatyczne powtórzenie.
- Pauza przed wykonaniem operacji zachowuje pending_action; checkpoint wskazuje model protokołu. Próba użycia stanu innego modelu jest odrzucana przed wywołaniem.
- Marka Kaelia pozostaje na agents.synviocore.xyz. Starsze adaptery i Codex CLI zachowują własne ścieżki.

## Dalej

1. Zweryfikować produkcyjne wdrożenie i zwykłe odczyty API bez płatnego wywołania. Dla przyszłego testu prawdziwego modelu osobno określić zakres i limit.
2. Zastąpić pozostałe tekstowe akcje w rozmowie/adaptacji dostawców natywnymi narzędziami tam, gdzie są zgodne; obecny etap obejmuje wykonawcę plików dla gpt-6-astra i gpt-6.1-sol.
3. Trwałe liczniki anti-loop obejmujące wznowienia i powtarzające się błędy walidacji. Ograniczone zmiany strategii; bez automatycznych kosztownych restartów.
4. Rozdzielenie danych workspace, Postgres jako źródło prawdy, rejestr modeli, pełniejsze Codex/MCP i rzeczywisty Live.
5. Potem księga portfela/płatności i studio. Najnowsze bonusy 1–10%; nie uruchamiać płatności bez księgi oraz weryfikacji zasad sprzedaży.

Zachować React/FastAPI i źródła OpenJarvis. Pełna architektura znajduje się w KAELIA_ARCHITECTURE.md w katalogu nadrzędnym sesji.

