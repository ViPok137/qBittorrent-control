import json
import os
import sys
import time
import logging
import configparser
from datetime import datetime, timedelta
import requests
from requests.exceptions import RequestException

# === ОПРЕДЕЛЕНИЕ ПУТЕЙ (Защита от автозапуска) ===
if getattr(sys, 'frozen', False):
    application_path = os.path.dirname(sys.executable)
else:
    application_path = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(application_path, "config.ini")
DATA_FILE = os.path.join(application_path, "torrent_data.json")
LOG_FILE = os.path.join(application_path, "torrent_log.log")


# === ИНИЦИАЛИЗАЦИЯ КОНФИГУРАЦИИ ===
def load_config():
    config = configparser.ConfigParser()
    if not os.path.exists(CONFIG_FILE):
        config['SETTINGS'] = {
            'QB_URL': 'http://localhost:8080',
            'USERNAME': 'admin',
            'PASSWORD': 'adminadmin',
            'DAYS_LIMIT': '14',
            'RATIO_THRESHOLD': '1.0',
            'LEECH_SUM_THRESHOLD': '14',
            'STARTUP_WAIT_SEC': '10'
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            config.write(f)
    else:
        config.read(CONFIG_FILE, encoding='utf-8')
    return config['SETTINGS']


conf = load_config()
QB_URL = conf.get('QB_URL')
USERNAME = conf.get('USERNAME')
PASSWORD = conf.get('PASSWORD')
DAYS_LIMIT = conf.getint('DAYS_LIMIT')
RATIO_THRESHOLD = conf.getfloat('RATIO_THRESHOLD')
LEECH_SUM_THRESHOLD = conf.getint('LEECH_SUM_THRESHOLD')
STARTUP_WAIT_SEC = conf.getint('STARTUP_WAIT_SEC')

session = requests.Session()


def setup_logger():
    """Настройка логирования в файл."""
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        encoding="utf-8"
    )


def load_data():
    """Загрузка базы данных торрентов из JSON."""
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_data(data):
    """Сохранение базы данных торрентов в JSON."""
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except IOError as e:
        logging.error(f"Ошибка сохранения JSON: {e}")


def login():
    """Авторизация в qBittorrent Web API."""
    try:
        r = session.post(f"{QB_URL}/api/v2/auth/login", data={"username": USERNAME, "password": PASSWORD}, timeout=10)
        if r.status_code != 200 or not session.cookies.get('SID'):
            raise Exception(f"Ошибка входа. Проверьте логин/пароль. Ответ: {r.text}")
    except RequestException as e:
        raise Exception(f"Не удалось подключиться к qBit: {e}")


def delete_torrent(hash_, name, reason=""):
    """Удаление торрента и его файлов."""
    try:
        logging.info(f"УДАЛЯЕМ: {name} | Причина: {reason}")
        print(f"❌ Удаляем: {name} ({reason})")
        session.post(f"{QB_URL}/api/v2/torrents/delete", data={"hashes": hash_, "deleteFiles": "true"}, timeout=10)
    except Exception as e:
        logging.error(f"Ошибка удаления {name}: {e}")


def main():
    setup_logger()

    # Пауза перед стартом
    print(f"⏳ Ожидание запуска qBittorrent ({STARTUP_WAIT_SEC} сек)...")
    for i in range(STARTUP_WAIT_SEC, 0, -1):
        print(f"Старт через {i} сек...", end="\r")
        time.sleep(1)
    print("🚀 Соединение с qBittorrent...          ")

    try:
        login()
        r = session.get(f"{QB_URL}/api/v2/torrents/info", timeout=15)
        r.raise_for_status()
        torrents = r.json()
    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
        print(f"💥 Ошибка: {e}")
        time.sleep(5)
        return

    data = load_data()
    now = datetime.now()
    today_str = now.date().isoformat()

    # Синхронизация активных торрентов
    active_hashes = {t["hash"] for t in torrents}
    data = {h: v for h, v in data.items() if h in active_hashes}

    stats = {"new": 0, "deleted_error": 0, "deleted_old": 0, "skipped": 0}

    for t in torrents:
        h = t["hash"]
        name = t["name"]
        state = t["state"]
        ratio = t.get("ratio", 0)
        leechs = t.get("num_leechs", 0)

        # 1. ПРОВЕРКА НА ОШИБКИ (Мгновенное удаление)
        # Статусы ошибок: ошибка, отсутствие файлов, неизвестный сбой
        error_states = ['error', 'missingFiles', 'unknown', 'stalled_error']
        if state in error_states:
            delete_torrent(h, name, reason=f"Статус ошибки: {state}")
            if h in data: del data[h]
            stats["deleted_error"] += 1
            continue

        # 2. ДОБАВЛЕНИЕ НОВЫХ В БАЗУ
        if h not in data:
            data[h] = {
                "ratio": ratio,
                "date": now.isoformat(),
                "leech_sum": leechs,
                "last_date": today_str
            }
            logging.info(f"НОВЫЙ ТОРРЕНТ: {name} добавлен в мониторинг")
            stats["new"] += 1
            continue

        # 3. ОБНОВЛЕНИЕ СТАТИСТИКИ (Раз в сутки)
        if data[h].get("last_date") != today_str:
            data[h]["leech_sum"] = data[h].get("leech_sum", 0) + leechs
            data[h]["last_date"] = today_str

        # 4. ПРОВЕРКА ЛИМИТОВ (По истечении DAYS_LIMIT)
        try:
            added_date = datetime.fromisoformat(data[h]["date"])
            if now - added_date >= timedelta(days=DAYS_LIMIT):
                ratio_delta = ratio - data[h].get("ratio", 0)
                leech_sum = data[h].get("leech_sum", 0)

                if ratio_delta < RATIO_THRESHOLD and leech_sum < LEECH_SUM_THRESHOLD:
                    delete_torrent(h, name, reason="Низкая активность (срок вышел)")
                    del data[h]
                    stats["deleted_old"] += 1
                else:
                    logging.info(f"АКТИВЕН: {name} (Delta Ratio: {ratio_delta:.2f}, Leech Sum: {leech_sum})")
            else:
                stats["skipped"] += 1
        except Exception as e:
            logging.error(f"Ошибка обработки торрента {name}: {e}")

    save_data(data)

    result_msg = f"Итог: Новых: {stats['new']}, Удалено ошибок: {stats['deleted_error']}, Удалено по лимиту: {stats['deleted_old']}, Оставлено: {stats['skipped']}"
    logging.info(result_msg)
    print(f"\n✅ {result_msg}")

    # Таймер перед закрытием
    print("\n" + "=" * 30)
    for i in range(5, 0, -1):
        print(f"Закрытие через {i} сек...", end="\r")
        time.sleep(1)
    print("До свидания!              ")


if __name__ == "__main__":
    main()