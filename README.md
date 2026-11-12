<p align="center">
  <img src="docs/images/banner.jpg" alt="Xray Wi-Fi Share" width="920">
</p>

<h1 align="center">Xray Wi‑Fi Share</h1>

<p align="center">
  <b>Раздай свой VLESS или Hysteria2 как обычную Wi‑Fi сеть</b><br>
  Подключай телефон, приставку, ноутбук и другие устройства без установки VPN‑клиента на них.
</p>

<p align="center">
  <a href="https://github.com/RasmusVraa/xray-wifi-share/releases/latest"><img src="https://img.shields.io/github/v/release/RasmusVraa/xray-wifi-share?style=for-the-badge&color=2BB673" alt="Release"></a>
  <a href="https://github.com/RasmusVraa/xray-wifi-share/releases/latest"><img src="https://img.shields.io/github/downloads/RasmusVraa/xray-wifi-share/total?style=for-the-badge&color=1A1F2B" alt="Downloads"></a>
  <img src="https://img.shields.io/badge/Windows-10%20%2F%2011-0E1218?style=for-the-badge" alt="Windows">
</p>

---

## Как это выглядит

<p align="center">
  <img src="docs/images/ui-preview.jpg" alt="Интерфейс Xray Wi-Fi Share" width="860">
</p>

## Что делает программа

1. Берёт твою ссылку **VLESS** или **Hysteria2**
2. Поднимает на компьютере отдельный VPN‑туннель
3. Раздаёт его как Wi‑Fi точку через USB‑адаптер
4. Устройства в этой сети ходят в интернет уже через VPN

Компьютер при этом продолжает пользоваться своим обычным интернетом — раздача не «перехватывает» весь ПК.

## Что нужно

- Windows 10 / 11
- USB Wi‑Fi адаптер, который умеет раздавать сеть (SoftAP / hosted network)
- Рабочая ссылка `vless://...` или `hysteria2://...` / `hy2://...`
- Права администратора при запуске раздачи (один раз через окно UAC)

## Установка

1. Скачай установщик из [Releases](https://github.com/RasmusVraa/xray-wifi-share/releases/latest):  
   **`XrayWifiShare-Setup-1.0.0.exe`**
2. Установи как обычную программу
3. Запусти **Xray Wi‑Fi Share** из меню Пуск

> Portable‑сборка без установщика тоже есть в релизе: папка `XrayWifiShare`.

## Быстрый старт

1. Вставь ссылку VLESS / Hysteria2  
   (кнопка **Вставить** или `Ctrl+V`)
2. Выбери USB Wi‑Fi адаптер
3. Задай имя сети и пароль (минимум 8 символов)
4. Нажми **Старт** и подтверди UAC
5. На другом устройстве подключись к этой Wi‑Fi сети

Готово: трафик устройства идёт через твой VPN.

## Кнопки

| Кнопка | Зачем |
|---|---|
| **Старт** | Запустить раздачу |
| **Стоп** | Корректно выключить точку и туннель |
| **Убить процессы** | Аварийно снести зависшую раздачу |
| **Показать** | Показать / скрыть пароль Wi‑Fi |
| **Обновить адаптеры** | Перечитать список Wi‑Fi адаптеров |

## Что сохраняется

Имя сети, пароль, ссылка и выбранный адаптер запоминаются между запусками  
(в `%AppData%\XrayWifiShare\settings.json`).

## Частые вопросы

**Устройство подключилось к Wi‑Fi, но сайты не открываются**  
Проверь, что в логе есть строка про успешную раздачу (`Раздача включена`). Нажми **Убить процессы**, затем снова **Старт**.

**Не находится Wi‑Fi адаптер**  
Подключи USB‑адаптер, установи драйвер, нажми **Обновить адаптеры**.  
Дешёвые no‑name адаптеры иногда не умеют режим точки доступа.

**Нужно ли держать программу открытой?**  
Да, пока раздача активна. Перед выходом лучше нажать **Стоп**.

**Это клиент для телефона?**  
Нет. Это раздача VPN с Windows‑ПК на другие устройства по Wi‑Fi.

## Безопасность

- Не публикуй свои `vless://` / `hysteria2://` ссылки
- Используй нормальный пароль на Wi‑Fi точку
- После тестовых сессий меняй пароль, если он светился на экране

## Скачать

👉 [Последний релиз](https://github.com/RasmusVraa/xray-wifi-share/releases/latest)

---

<p align="center">
  <sub>Windows · VLESS · Hysteria2 · SoftAP</sub>
</p>
