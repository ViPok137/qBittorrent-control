import json
import os
import sys
import time
import logging
import configparser
from datetime import datetime, timedelta
import requests
from requests.exceptions import RequestException

# === ОПРЕДЕЛЕНИЕ ПУТЕЙ ===
if getattr(sys, 'frozen', False):
    application_path = os.path.dirname(sys.executable)
else:
    application_path = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(application_path, "config.ini")
DATA_FILE = os.path.join(application_path, "torrent_data.json")
LOG_FILE = os.path.join(application_path, "torrent_log.log")

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

# === ЛОГГЕР ===
def setup_logger():
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        encoding="utf-8"
    )

def clean_old_logs():
    if not os.path.exists(LOG_FILE):
        return
    cutoff = datetime.now() - timedelta(days=30)
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    fresh_lines = []
    for line in lines:
        try:
            log_date = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
            if log_date >= cutoff:
                fresh_lines.append(line)
        except ValueError:
            fresh_lines.append(line)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.writelines(fresh_lines)

# === ДАННЫЕ ===
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"torrents": {}, "blacklist": []}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            content = json.load(f)
            if "torrents" not in content:
                return {"torrents": content, "blacklist": []}
            return content
    except (json.JSONDecodeError, IOError):
        return {"torrents": {}, "blacklist": []}

def save_data(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except IOError as e:
        logging.error(f"Ошибка сохранения JSON: {e}")

# === ПОДКЛЮЧЕНИЕ ===
def login():
    try:
        r = session.post(f"{QB_URL}/api/v2/auth/login", data={"username": USERNAME, "password": PASSWORD}, timeout=10)
        if r.status_code != 200 or not session.cookies.get('SID'):
            raise Exception(f"Ошибка входа. Ответ: {r.text}")
        logging.info("Успешная авторизация в qBittorrent")
    except RequestException as e:
        raise Exception(f"Не удалось подключиться к qBit: {e}")

# === RSS ИСКЛЮЧЕНИЯ ===
def add_to_rss_exclude(torrent_name):
    try:
        rules_r = session.get(f"{QB_URL}/api/v2/rss/rules")
        if rules_r.status_code != 200:
            return
        rules = rules_r.json()
        for rule_name, rule_data in rules.items():
            current_exclude = rule_data.get('mustNotContain', '').strip()
            if torrent_name in current_exclude:
                continue
            new_exclude = f"{current_exclude}|{torrent_name}" if current_exclude else torrent_name
            rule_data['mustNotContain'] = new_exclude
            session.post(f"{QB_URL}/api/v2/rss/setRule", data={'ruleName': rule_name, 'ruleDef': json.dumps(rule_data)})
            logging.info(f"RSS: Добавлено исключение '{torrent_name}' в правило '{rule_name}'")
            print(f"🚫 RSS: Исключение добавлено в правило '{rule_name}'")
    except Exception as e:
        logging.error(f"Ошибка обновления RSS исключений: {e}")

# === УДАЛЕНИЕ ===
def delete_torrent(hash_, name, reason=""):
    try:
        logging.info(f"УДАЛЯЕМ: {name} | Причина: {reason}")
        print(f"❌ Удаляем: {name} ({reason})")
        session.post(f"{QB_URL}/api/v2/torrents/delete", data={"hashes": hash_, "deleteFiles": "true"}, timeout=10)
    except Exception as e:
        logging.error(f"Ошибка удаления {name}: {e}")

# === ОСНОВНАЯ ЛОГИКА ===
def main():
    setup_logger()
    clean_old_logs()

    logging.info("=" * 50)
    logging.info("Запуск скрипта")

    print(f"⏳ Ожидание {STARTUP_WAIT_SEC} сек...")
    for i in range(STARTUP_WAIT_SEC, 0, -1):
        print(f"Старт через {i} сек...", end="\r")
        time.sleep(1)

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

    full_data = load_data()
    data = full_data["torrents"]
    blacklist = full_data["blacklist"]

    # Ограничение чёрного списка
    blacklist = blacklist[-500:]

    now = datetime.now()
    today_str = now.date().isoformat()

    active_hashes = {t["hash"] for t in torrents}
    data = {h: v for h, v in data.items() if h in active_hashes}

    stats = {"new": 0, "deleted_error": 0, "deleted_old": 0, "blacklisted": 0, "waiting": 0, "skipped_downloading": 0}

    # Статусы скачивания — пропускаем
    downloading_states = {'downloading', 'stalledDL', 'checkingDL', 'pausedDL', 'queuedDL', 'metaDL', 'forcedDL'}

    error_states = {'error', 'missingFiles', 'unknown', 'stalled_error'}

    for t in torrents:
        h = t["hash"]
        name = t["name"]
        state = t["state"]
        ratio = t.get("ratio", 0)
        leechs = t.get("num_leechs", 0)

        # 1. ПРОПУСК СКАЧИВАЮЩИХСЯ
        if state in downloading_states:
            logging.info(f"ПРОПУСК (скачивается): {name} | Статус: {state}")
            stats["skipped_downloading"] += 1
            continue

        # 2. ЧЁРНЫЙ СПИСОК
        if h in blacklist:
            delete_torrent(h, name, reason="В черном списке (ранее ошибка)")
            stats["blacklisted"] += 1
            continue

        # 3. ПРОВЕРКА НА ОШИБКИ
        if state in error_states:
            add_to_rss_exclude(name)
            delete_torrent(h, name, reason=f"Статус ошибки: {state}")
            if h not in blacklist:
                blacklist.append(h)
            if h in data:
                del data[h]
            stats["deleted_error"] += 1
            continue

        # 4. НОВЫЙ ТОРРЕНТ
        if h not in data:
            data[h] = {"ratio": ratio, "date": now.isoformat(), "leech_sum": leechs, "last_date": today_str}
            logging.info(f"НОВЫЙ: {name}")
            stats["new"] += 1
            continue

        # 5. ОБНОВЛЕНИЕ ЛИЧЕЙ (только если день изменился)
        if data[h].get("last_date") != today_str:
            data[h]["leech_sum"] = data[h].get("leech_sum", 0) + leechs
            data[h]["last_date"] = today_str

        # 6. ПРОВЕРКА СРОКА
        try:
            added_date = datetime.fromisoformat(data[h]["date"])
            days_passed = (now - added_date).days
            days_left = DAYS_LIMIT - days_passed

            if days_passed >= DAYS_LIMIT:
                ratio_delta = ratio - data[h].get("ratio", 0)
                leech_sum = data[h].get("leech_sum", 0)
                logging.info(f"ПРОВЕРКА: {name} | Δratio={ratio_delta:.2f} | leech_sum={leech_sum}")
                if ratio_delta < RATIO_THRESHOLD and leech_sum < LEECH_SUM_THRESHOLD:
                    delete_torrent(h, name, reason="Низкая активность (срок истек)")
                    del data[h]
                    stats["deleted_old"] += 1
                else:
                    logging.info(f"ОСТАВЛЯЕМ: {name}")
                    data[h] = {"ratio": ratio, "date": now.isoformat(), "leech_sum": 0, "last_date": today_str}
            else:
                logging.info(f"ЖДЁТ: {name} | Осталось дней: {days_left}")
                stats["waiting"] += 1
        except Exception as e:
            logging.error(f"Ошибка обработки {name}: {e}")

    save_data({"torrents": data, "blacklist": blacklist})

    # === ИТОГИ ===
    summary = (
        f"Итоги: новых={stats['new']} | "
        f"скачиваются={stats['skipped_downloading']} | "
        f"ждут срока={stats['waiting']} | "
        f"удалено ошибок={stats['deleted_error']} | "
        f"удалено по лимиту={stats['deleted_old']} | "
        f"повторов={stats['blacklisted']}"
    )
    logging.info(summary)
    logging.info("Завершение скрипта")
    print(f"\n✅ {summary}")
    time.sleep(5)

if __name__ == "__main__":
    main()