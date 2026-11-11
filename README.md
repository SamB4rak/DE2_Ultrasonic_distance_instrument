# DE2 Ultrasonic distance instrument
Ultrasonic sensors, serial 4-digit display, or Nokia SPI display  
#### Autoři:
* Samuel Barák
* Daniel Blumaier
* Samuel Breza
* Denis Bureš

### Zadání:
Projekt ve kterém pomoci ultrazvukového senzoru a displeje se vytvoří zařízení, které v pravidelných intervalech měří vzdálenost a ukazuje ji na displeji. Zároveň poskytuje vizuální i audiovou indikaci toho, zda je objekt blízko nebo daleko.

## Využité součástky
* Projekt je vytvářen v **MicroPythonu** na desku **Rasberry pi pico W**

| **Komponenty** | **Funkce** |
| :---: | :---: | 
| **HC‑SR04** | Ultrazvukový signál, který vysílá ultrazvukové vlny. Při odrazu vlny zpět od dané překážky, posílá data zpět. |
| **BME/BMP280** |  |
| **OLED Displej** | Pro vizualní indikaci za pomoci zobrazení vzdálenosti od objektu. |
| **Tlačítko** | Pro zapnutí a vypnutí senzoru. |
| **RGB LED** | Pro vizuální inidikaci za pomoci svícení dané barvy a rychlosti blikaní. |
| **Relé modul** | Pro audio indikaci za pomoci cvakání. |
