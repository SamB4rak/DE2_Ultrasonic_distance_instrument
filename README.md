# DE2 Ultrasonic distance instrument
Ultrasonic sensors, serial 4-digit display, or Nokia SPI display  
#### Autoři:
* Samuel Barák
* Daniel Blumaier
* Samuel Breza
* Denis Bureš

### Zadání:
Projekt, ve kterém se pomocí ultrazvukového senzoru a displeje vytvoří zařízení, které v pravidelných intervalech měří vzdálenost a ukazuje ji na displeji. Zároveň poskytuje vizuální i zvukovou indikaci toho, zda je objekt blízko, nebo daleko.

## Využité součástky
* Projekt je vytvářen v **MicroPythonu** na desku **Raspberry Pi Pico W**

| **Komponenty** | **Funkce** |
| :---: | :---: |
| **HC‑SR04** | Ultrazvukový senzor (2×, vlevo a vpravo), který vysílá ultrazvukové vlny. Podle doby, za kterou se vlna odražená od překážky vrátí, se vypočítá vzdálenost. |
| **BME/BMP280** | Měří teplotu, vlhkost a tlak vzduchu (BMP280 vlhkost neměří). Hodnoty zobrazuje na displeji a každých 20 s je přes Wi-Fi odesílá na ThingSpeak, stejně jako ve cvičení 7. Teplotu lze navíc využít ke zpřesnění rychlosti zvuku, a tím i měřené vzdálenosti. |
| **OLED Displej** | Pro vizuální indikaci za pomoci zobrazení vzdálenosti od objektu a hodnot z BME280. |
| **Tlačítko** | Pro zapnutí a vypnutí senzorů. |
| **RGB LED** | Pro vizuální indikaci za pomoci svícení dané barvy (zelená = daleko, žlutá = středně, červená = blízko) a rychlosti blikání. |
| **Relé modul** | Pro zvukovou indikaci za pomoci cvakání ve stejném rytmu, v jakém bliká LED. |

## Schémata
* **Blokové schéma:** [![Schematic Preview](ultrasonic_block_diagram.jpg)](ultrasonic_block_diagram.svg)
