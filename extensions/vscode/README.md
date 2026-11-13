# SYNVIO Workspace 0.1

Pierwsza wtyczka lokalnego agenta SYNVIO do desktopowego VS Code.

## Instalacja i użycie

1. Zainstaluj plik VSIX przez **Extensions → … → Install from VSIX**.
2. Otwórz i zatwierdź zaufanie do folderu projektu.
3. W palecie poleceń wybierz **SYNVIO: Open Agent**.
4. Kliknij **Zaloguj** i użyj konta aplikacji SYNVIO (nie hasła panelu Supabase).
5. Podaj polecenie i budżet modelu w USD, a następnie wybierz **Wyślij do agenta**.
6. Przeczytaj zgodę na przekazywanie kodu wybranego folderu do SYNVIO i dostawcy modelu.
7. Proponowane zapisy otwierają podgląd różnic. Dopiero **Zastosuj** zapisuje plik.
8. **Stop** w panelu lub pasku VS Code przerywa lokalne akcje i blokuje kolejne kroki.

## Co działa w tej wersji

- Logowanie Supabase i sesja w VS Code SecretStorage; brak zapisanego hasła.
- Klucz modelu pozostaje na backendzie SYNVIO, poza wtyczką i projektem.
- Agent listuje do 100 plików, czyta pliki tekstowe do 16 KB i proponuje zapisy.
- Zmiana ograniczona do wybranego folderu; blokowane ścieżki absolutne, przejścia
  `..`, dowiązania, ukryte katalogi i znane pliki poświadczeń.
- Podgląd różnic, osobne potwierdzenie każdego zapisu i kontrola konfliktu pliku.
- Maksymalnie 8 kroków; budżet wspólny dla całego polecenia kontrolowany przez serwer.
- Follow-upy zachowują ograniczoną historię w bieżącym panelu i folderze.
- Powtórzony krok HTTP odtwarza zapisaną odpowiedź, nie uruchamia modelu ponownie.

To **nie jest jeszcze pełny odpowiednik Codexa**. Nie ma uruchamiania terminala,
testów, obrazu pulpitu, sterowania myszą ani przeglądarką. Zmiany lokalne mają
uprawnienia użytkownika VS Code: nie jest to izolacja maszyny wirtualnej.
Znane nazwy sekretów są blokowane, ale wtyczka nie wykryje każdego klucza
wklejonego do zwykłego pliku źródłowego — przeglądaj udostępniany projekt.

Budżet dotyczy szacowanego kosztu modelu, bez infrastruktury, podatków i marży.
Przerwanie żądania nie gwarantuje anulowania kosztu już rozpoczętej generacji.
Nie ma automatycznych ponowień płatnych kroków. Backend musi mieć podłączony
model i magazyn Redis. Sesja testowa jest obecnie ograniczona listą kont właściciela.

Historia panelu nie jest jeszcze synchronizowana z historią zadań strony WWW.
Zamknięcie/odświeżenie panelu traci lokalny kontekst rozmowy; token logowania
pozostaje w SecretStorage do wylogowania lub wygaśnięcia. Wersja nie jest
opublikowana w Marketplace i nie została jeszcze zweryfikowana płatnym zadaniem.

## Weryfikacja

`npm test` uruchamia testy offline dla ograniczeń plików, logowania i komunikacji.
Backend ma dodatkowe testy budżetu, idempotencji, Stop i zakresu tokenów.
Żaden z tych testów nie wywołuje płatnego API.
