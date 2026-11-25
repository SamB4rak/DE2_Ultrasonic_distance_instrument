# DE2 Ultrasonic distance instrument
Ultrasonic sensors, serial 4-digit display, or Nokia SPI display  
#### Autoři:
* Samuel Barák
* Daniel Blumaier
* Samuel Breza
* Denis Bureš

## Zadání:
Projekt, ve kterém se pomocí dvou ultrazvukových senzorů a displeje vytvoří zařízení, které v pravidelných intervalech měří vzdálenost a ukazuje ji na displeji. Zároveň poskytuje vizuální i zvukovou indikaci toho, zda je objekt blízko, nebo daleko. Podobně jako bezpečnostní kamera navíc zaznamenává události (změny zóny, příchod a odchod objektu a pravidelná měření) s přesným časem a přes Wi-Fi je odesílá do cloudu ThingSpeak.

## Funkčnost:
* Dva senzory (vlevo a vpravo) měří střídavě, každý 8× za sekundu. Každou hodnotu vyhladí medián ze tří posledních měření a dál se počítá s bližší z obou vzdáleností. Podle ní se určí zóna:

| **Zóna** | **Vzdálenost** | **Barevné svícení** |
| :--- | :---: | :---: |
| blízko | pod 0,3 m | červená |
| středně | 0,3–1,0 m | žlutá |
| daleko | nad 1,0 m | zelená |
| volno | nic v dosahu 4 m | zelená, svítí trvale |

* Hranice zón mají hysterezi 3 cm, aby barva na hranici nepřeskakovala. LED bliká a relé při každém bliknutí cvakne: od 1 m dál jednou za sekundu, a čím blíž, tím rychleji, až 5× za sekundu na 10 cm a blíž. Displej ukazuje vzdálenosti z obou senzorů, poslední událost a stav Wi-Fi.

| **Událost** | **Barevné svícení** |
| :--- | :---: |
| Vypnuté měření | modrá |
| Porucha | fialová |

*Události se ukládají s časem z internetu (NTP) a každých 20 s se odešlou do ThingSpeaku:
| **Událost** | **Kdy vznikne** |
| :--- | :--- |
| `SAMPLE` | každou minutu, se současnými vzdálenostmi |
| `ZONE` | při změně zóny |
| `ENTER` | objekt je blíž než 1 m (dvě měření po sobě) |
| `LEAVE` | objekt je 3 s dál než 1 m nebo zmizel; zapíše se i nejmenší vzdálenost a doba přítomnosti |
| `MODE` | tlačítkem se měření zapnulo nebo vypnulo |

Kanál ThingSpeak má 7 polí: 
* Vzdálenost 
* Levý a pravý senzor (v metrech) 
* Zóna 
* Událost 
* Nejmenší vzdálenost (m)  
* Doba přítomnosti (s)

## Princip měření
Pico pošle na vstup *Trig* impulz dlouhý 10 µs. Senzor vyšle osm ultrazvukových pulzů o frekvenci 40 kHz a výstup *Echo* drží v log. 1, dokud se ozvěna od překážky nevrátí. Délka impulzu *Echo* je tedy doba *t*, za kterou zvuk doletí k překážce a zpět, proto se dělí dvěma:

$d = \frac{v \cdot t}{2}$

kde *v* = 343,4 m/s je rychlost zvuku ve vzduchu při 20 °C. Například *t* = 5,8 ms odpovídá vzdálenosti 1 m. Senzor měří zhruba od 2 cm do 4 m. Senzory se střídají po 60 ms, aby jeden nezachytil ozvěnu druhého.


## Využité součástky
* Projekt je vytvářen v **MicroPythonu 1.29.0** na desku **Raspberry Pi Pico W**

| **Komponenty** | **Počet** | **Funkce** |
| :---: | :---: | :---: |
| [HC‑SR04](https://cdn.sparkfun.com/datasheets/Sensors/Proximity/HCSR04.pdf) | 2 | Ultrazvukový senzor (vlevo a vpravo), který vysílá ultrazvukové vlny. Podle doby, za kterou se vlna odražená od překážky vrátí, se vypočítá vzdálenost. Jeho výstup Echo má 5 V, proto ho dělič napětí 1 kΩ / 2 kΩ snižuje na 3,3 V, které snese Pico. |
| [Raspberry Pi Pico W](https://pip-assets.raspberrypi.com/categories/686-raspberry-pi-pico-w/documents/RP-008312-DS-2-pico-w-datasheet.pdf) | 1 | Řídí celé zařízení. Přes vestavěnou Wi-Fi odesílá do cloudu ThingSpeak záznam událostí, podobně jako bezpečnostní kamera: změny zóny, příchod a odchod objektu (s nejmenší vzdáleností a dobou přítomnosti) a pravidelné měření každou minutu, vše s časem přesným na sekundu. |
| [OLED Displej](https://robu-prod-media.s3.ap-south-1.amazonaws.com/uploads/2019/12/1.3-Inch-I2C-IIC-OLED-LCD-Module-4pin-with-VCC-GND-Blue-1.pdf) | 1 | Pro vizuální indikaci za pomoci zobrazení vzdáleností z obou senzorů, poslední zaznamenané události a stavu Wi-Fi a odesílání. |
| **Tlačítko** | 1 | Pro zapnutí a vypnutí senzorů. |
| [RGB LED](https://arwill.hu/forras/termek/kit-ek/ky-016-rgb-led-modul-506261.pdf) | 1 | Pro vizuální indikaci za pomoci svícení dané barvy (zelená = daleko, žlutá = středně, červená = blízko) a rychlosti blikání. |
| [Relé modul](https://dratek.cz/docs/produkty/0/579/1517442331.pdf) | 1 | Pro zvukovou indikaci za pomoci cvakání ve stejném rytmu, v jakém bliká LED. |
| **Rezistory (1kΩ a 2kΩ)** | 2 a 2 | Pro vytvoření děliče, který z 5 V udělá 3,3 V bezpečné pro piny Raspberry Pi Pico W |
| **Rezistory (10 kΩ)** | 2 | Drží vstup Trig na bezpečném napětí, dokud ho Pico při startu neovládá. |
| **Rezistory (4,7 kΩ)** | 3 | Pull-upy pro relé a pro I2C displeje. |
| **Kondenzátory (100 nF a 10–47 µF)** | 3 a 1 | Stabilizují napájení senzorů a relé. |
* Ochrana: Ultrazvukový senzor HC-SR04 bude správně fungovat s napájecím napětím 5 V. Je proto připojený na pin *VBUS*. Signál, který je pak posílaný přes pin *ECHO* má taktéž 5 V nicméně piny Raspberry Pi Pico W zvládnou jen 3,3 V. Proto pomocí 2 rezistorů se vytvoří napěťový dělič 2/3, který napětí z 5 V sníží na 3,3 V.
## Schémata
* **Blokové schéma:** [![Blokové schéma](docs/img/ultrasonic_block_diagram.jpg)](docs/img/ultrasonic_block_diagram.svg)

* **Schéma zapojení:** [![Schéma zapojení](docs/img/ultrasonic_wiring_diagram.jpg)](docs/img/ultrasonic_wiring_diagram.svg)

| **Modul** | **Zapojení** |
| :--- | :--- |
| HC‑SR04 #1 (vlevo) | VCC → 5 V (VBUS, pin 40), Trig → GP20 (pin 26) + 10 kΩ na GND, Echo → dělič 1 kΩ / 2 kΩ → GP21 (pin 27), GND → GND |
| HC‑SR04 #2 (vpravo) | VCC → 5 V (VBUS, pin 40), Trig → GP18 (pin 24) + 10 kΩ na GND, Echo → dělič 1 kΩ / 2 kΩ → GP19 (pin 25), GND → GND |
| Tlačítko | GP17 (pin 22) a GND |
| RGB LED | R → GP12 (pin 16), G → GP13 (pin 17), B → GP14 (pin 19), − → GND |
| Relé modul | DC+ → 5 V (VBUS, pin 40), DC− → GND, IN → GP16 (pin 21) + 4,7 kΩ na 3V3 |
| OLED displej | VCC → 3V3 (pin 36), GND → GND, SCL → GP5 (pin 7), SDA → GP4 (pin 6) + 4,7 kΩ na 3V3 |

Senzory a relé běží z 5 V, displej z 3,3 V. Všechny země jsou spojené a celé zařízení se napájí přes USB. Dělič u Echo: *U* = 5 V · 2 kΩ / (1 kΩ + 2 kΩ) ≈ 3,33 V.

## Časování
Program běží v `asyncio`: každá úloha se pravidelně probudí, udělá svou práci a zase uspí.

| **Úloha** | **Jak často** | **Co dělá** |
| :--- | :---: | :--- |
| Tlačítko | 20 ms | Zapíná a vypíná senzory. Zákmity tlačítka trvají do 10 ms, takže žádný stisk neunikne. |
| Měření | 60 ms | Střídá levý a pravý senzor, každý měří 8× za sekundu. Kratší odstup by zachytil ozvěnu předchozího měření. |
| Události | po každém měření | Zapíše změnu zóny, příchod a odchod objektu a jednou za minutu aktuální vzdálenosti. |
| LED a relé | 50 ms | Barva podle zóny, bliknutí a cvaknutí 1–5× za sekundu: čím blíž, tím rychleji. |
| Displej | 200 ms | Vzdálenosti, poslední událost a stav Wi-Fi. Obraz se posílá po částech, aby nezdržoval měření. |
| Cloud | 20 s | Pošle nové události do ThingSpeaku, který přijímá data nejdřív po 15 s. Čas si bere z NTP po připojení a pak každých 6 h. |
| Echo | při každé hraně | Přerušení jen zapíše čas hrany, vzdálenost spočítá úloha měření. |

## Software
* **MicroPython 1.29.0** (firmware `RPI_PICO_W`)
* **Vestavěné moduly:** `asyncio`, `machine`, `time`, `network`, `ntptime`, `socket`, `json`, `framebuf`, `micropython`, `sys`
* **Ovladač displeje:** `sh1106.py` od [robert-hh](https://github.com/robert-hh/SH1106) (licence MIT)
* **Na počítači:** `mpremote` (nahrávání do Pica), `mpy-cross` (kontrola kódu) a `pytest` (testy)

## Rozdělení práce v týmu
Na zapojení a testování pracujeme všichni společně.

| **Člen týmu** | **Na starosti** |
| :--- | :--- |
| Samuel Barák | návrh programu, propojení modulů a testy, drobné úpravy repozitáře |
| Daniel Blumaier | plakát, prezentace a video, fotky, doplňky |
| Samuel Breza | hardware: zapojení, senzory s děliči, displej, LED, relé a tlačítko; měření přesnosti |
| Denis Bureš | větší úpravy repozitáře a dokumentace (README, Sphinx, výsledky testů) |
