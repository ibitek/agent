# Kaelia · uruchomienie domeny i integracji

## Przed zakupem

- Uruchom `npm run test:browser` i `python -m pytest tests/test_local_browser.py tests/test_video_render.py tests/test_video_studio.py`. Testy symulują dostawców i działają bez płatnego API.
- Zachowaj produkcję pod `agents.synviocore.xyz` jako działający punkt powrotu. Nowa domena trafi do tego samego projektu Vercel.
- Pakiet rozszerzenia można zbudować z wyprzedzeniem dla dowolnego dokładnego adresu HTTPS. Do wydania dla klienta użyj potwierdzonego adresu aplikacji.

## Domena i pierwsza próba

1. Dodaj nową domenę do aktualnego projektu Kaelii. Wprowadź rekordy wskazane przez Vercel u operatora DNS. Potwierdź działanie HTTPS na nowym adresie.
2. Ustaw `SYNVIO_PUBLIC_URL` na nowy adres aplikacji i wdroż ponownie. Ten adres jest używany przez referencje wideo i wywołania Codexa. Sprawdź logowanie ponownie na nowej domenie, ponieważ sesja przeglądarki jest przypisana do hosta. Jeśli aktywujesz logowanie z przekierowaniem, dodaj do Supabase jego dokładny adres powrotu.
3. Zbuduj rozszerzenie dla nowego adresu, np. `python scripts/package_browser_extension.py --origin https://usekaelia.xyz --out ../Kaelia-Local-Browser.zip`. Przed instalacją zweryfikuj docelową domenę. Poprzednią paczkę można zachować na próbę powrotu.
4. Zacznij od bezpłatnego przebiegu. Zaloguj się, otwórz **Browser → Local tab**, udostępnij osobną kartę, uruchom „pokaż linki”, zatrzymaj dostęp i sprawdź, czy zamknięcie karty lub zmiana domeny go cofa.
5. Klucz `OPENAI_API_KEY` wprowadź w ustawieniach Production w Vercel. Po wdrożeniu panel potwierdzi obecność konfiguracji. To sygnał, że klucz został ustawiony. Dostęp do modelu i saldo potwierdzi dopiero pierwsze rzeczywiste wywołanie.
6. Po zatwierdzeniu kosztu uruchom jedno krótkie zadanie badawcze. Sprawdź wynik, źródła, widoczne działania i wpis kosztu. Render wideo sprawdź osobno, po wycenie jednej sceny. Wideo korzysta z AI Gateway, więc doładowanie OpenAI nie potwierdza salda tego dostawcy.
7. Po udanej próbie na nowej domenie zdecyduj o przekierowaniu starego adresu. W razie problemu pozostaw oba adresy podpięte, przywróć poprzedni `SYNVIO_PUBLIC_URL` i użyj poprzedniej paczki rozszerzenia.

[Dodawanie domeny w Vercel](https://vercel.com/docs/domains/working-with-domains/add-a-domain) · [Adresy przekierowań Supabase](https://supabase.com/docs/guides/auth/redirect-urls)

## Synvio Core po próbie przeglądarkowej

1. W panelu Synvio pozostaje Copilot. Dla jednej organizacji testowej przygotuj opcję „Połącz z Kaelią” i dopnij identyfikator organizacji, pracownika i zgłoszenia do każdego zadania. Na tym etapie konto po stronie Synvio nadal kontroluje dostęp.
2. Podłącz jedno działanie odczytu przez istniejącą bramkę Shopify. Scenariusze odbioru obejmują wynik dla pracownika z dostępem, odmowę dla obcego zgłoszenia i poprawną obsługę braku połączenia. Lokalnie użyj fikcyjnych zgłoszeń i zamówień.
3. Dodaj rezerwację i rozliczenie w portfelu Synvio. Przebieg z Kaelii rozlicza się jednokrotnie po identyfikatorze. Modele tego trybu finansuje usługa. Wybór BYOK jest wyłączony dla zadań Kaelii.
4. Po pomyślnej próbie odczytu i rozliczenia dodaj zapisy wewnętrzne, następnie wysyłkę wiadomości i operacje na zamówieniach. Każda operacja zewnętrzna ma podgląd skutków, kontrolę uprawnień przed wykonaniem i weryfikację wyniku.
5. Dostępy do usług podłączaj przez autoryzację dostawcy. Dla paneli bez API pracownik loguje się sam i udostępnia zalogowaną kartę. Sekrety wykorzystywane przez integracje serwerowe trafiają do magazynu poświadczeń, a instrukcje operacyjne do osobnych ustawień organizacji.
