# Discord bilietų botas

Šis projektas suteikia pilnai funkcionuojantį Discord bilietų (ticket) botą. Jis sukurtas taip, kad būtų lengvai pritaikomas jūsų serverio poreikiams, palaiko individualius bilietus, interaktyvią panelę su kategorijų pasirinkimu ir transkriptų išsaugojimą.

## Galimybės

- `/ticketpanel` komanda išsiunčia panelę su kategorijų pasirinkimu bilietų kūrimui.
- Vartotojas, pasirinkęs kategoriją, užpildo formą (tema + aprašymas) ir gauna privatų kanalą su komanda.
- Kiekviename biliete pateikiamas uždarymo mygtukas, leidžiantis savininkui arba personalui uždaryti pokalbį.
- Uždarant bilietą galima nurodyti priežastį; pasirinktai nustatytas kanalas gauna pranešimą ir tekstinį transkriptą.
- Bilietų numeracija ir būsena išsaugoma `tickets_state.json`, todėl kanalų pavadinimai nuoseklūs.

## Diegimas

1. **Reikalingi paketai**

   ```bash
   python -m pip install -r requirements.txt
   ```

2. **Sukurkite Discord aplikaciją ir botą**

   - [Discord Developer Portal](https://discord.com/developers/applications) susikurkite programą.
   - „Bot“ skiltyje sugeneruokite tokeną (papildomų „message content“ intencijų nereikia).
   - Pakvieskite botą į serverį su `applications.commands` ir `bot` scope.

3. **Konfigūracija**

   - Nukopijuokite `config.example.json` į `config.json`.
   - Užpildykite laukus:
     - `token` – jūsų boto tokenas.
     - `staff_role_id` – rolės ID, kuri turės prieigą prie bilietų (0, jei nenaudojama).
     - `ticket_category_name` – kategorijos pavadinimas, kurioje bus kuriami bilietai (jei nėra – sukuriama automatiškai).
     - `transcript_channel_id` – kanalo ID, kuriame gausite pranešimus bei transkriptus (arba `null`, jei nereikia).

4. **Paleidimas**

   ```bash
   python bot.py
   ```

   Pirmo paleidimo metu, jei `config.json` nerastas, botas informuos apie būtinybę jį sukurti.

5. **Naudojimas serveryje**

   - Parašykite `/ticketpanel` kanale, kuriame norite matyti bilietų panelę. Komandą gali vykdyti tik vartotojai su „Manage Server“ leidimu.
   - Nariai, pasirinkę tinkamą kategoriją iš išskleidžiamojo sąrašo, gaus modalinę formą, o po užpildymo – privatų kanalą.
   - Mygtukas „Uždaryti bilietą“ leidžia savininkui ar personalui pasirinktinai nurodyti priežastį ir archyvuoti kanalą. Jei nustatytas `transcript_channel_id`, ten bus išsiųstas transkriptas.

## Naudingi patarimai

- `tickets_state.json` failas sukuriamas automatiškai ir saugo paskutinio bilieto numerį. Jei norite pradėti numeraciją iš naujo, pašalinkite šį failą (kai botas išjungtas).
- Kategorijų pavadinimus ir aprašymus galite koreguoti `bot.py` faile esančiame `TICKET_CATEGORIES` sąraše.
- Jei pakeičiate boto kodą, nepamirškite perkrauti proceso.
- `config.json` saugokite tik lokaliai – jame esantis tokenas suteikia pilną prieigą prie boto paskyros.

## Licencija

Projektas pateikiamas „as is“ principu. Naudokite, adaptuokite ir plėskite pagal savo poreikius.
