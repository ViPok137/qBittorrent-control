import json
import os
import sys
import time
import logging
import configparser
import shutil
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
            'CHECK_INTERVAL_HOURS': '1',
            'DISK_THRESHOLD_PERCENT': '90',
            'RSS_RULE_NAME': ''
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            config.write(f)
        print(f"⚙️ Создан config.ini — заполни RSS_RULE_NAME названием правила RSS")
    else:
        config.read(CONFIG_FILE, encoding='utf-8')

    settings = config['SETTINGS']

    try:
        threshold = int(settings.get('DISK_THRESHOLD_PERCENT', '90'))
        if not 1 <= threshold <= 100:
            raise ValueError
    except ValueError:
        print("❌ DISK_THRESHOLD_PERCENT должен быть от 1 до 100. Установлено значение по умолчанию: 90")
        logging.warning("DISK_THRESHOLD_PERCENT вне диапазона 1-100, используется 90")
        config['SETTINGS']['DISK_THRESHOLD_PERCENT'] = '90'
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            config.write(f)

    try:
        interval = int(settings.get('CHECK_INTERVAL_HOURS', '1'))
        if interval < 1:
            raise ValueError
    except ValueError:
        print("❌ CHECK_INTERVAL_HOURS должен быть >= 1. Установлено значение по умолчанию: 1")
        config['SETTINGS']['CHECK_INTERVAL_HOURS'] = '1'
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            config.write(f)

    return config['SETTINGS']

conf = load_config()
QB_URL = conf.get('QB_URL')
USERNAME = conf.get('USERNAME')
PASSWORD = conf.get('PASSWORD')
DAYS_LIMIT = conf.getint('DAYS_LIMIT')
RATIO_THRESHOLD = conf.getfloat('RATIO_THRESHOLD')
LEECH_SUM_THRESHOLD = conf.getint('LEECH_SUM_THRESHOLD')
CHECK_INTERVAL_HOURS = conf.getint('CHECK_INTERVAL_HOURS')
DISK_THRESHOLD_PERCENT = conf.getint('DISK_THRESHOLD_PERCENT')
RSS_RULE_NAME = conf.get('RSS_RULE_NAME', '').strip()

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

# === ПОЛУЧЕНИЕ ПУТИ ИЗ ПРАВИЛА RSS ===
def get_rss_rule_save_path(rule_name):
    try:
        r = session.get(f"{QB_URL}/api/v2/rss/rules", timeout=10)
        if r.status_code != 200:
            return None
        rules = r.json()
        if rule_name not in rules:
            logging.warning(f"Правило RSS '{rule_name}' не найдено")
            return None
        save_path = rules[rule_name].get('savePath', '').strip()
        if not save_path:
            logging.warning(f"Правило RSS '{rule_name}' не имеет пути сохранения")
            return None
        return save_path
    except Exception as e:
        logging.error(f"Ошибка получения пути RSS правила: {e}")
        return None

# === ПРОВЕРКА ДИСКА ===
def get_disk_usage_percent(path):
    try:
        usage = shutil.disk_usage(path)
        return round(usage.used / usage.total * 100, 1)
    except Exception as e:
        logging.error(f"Ошибка проверки диска '{path}': {e}")
        return None

# === УПРАВЛЕНИЕ RSS ===
def set_rss_rule_enabled(rule_name, enabled: bool):
    try:
        r = session.get(f"{QB_URL}/api/v2/rss/rules", timeout=10)
        if r.status_code != 200:
            return
        rules = r.json()
        if rule_name not in rules:
            logging.warning(f"Правило RSS '{rule_name}' не найдено")
            return
        rule_data = rules[rule_name]
        rule_data['enabled'] = enabled
        session.post(
            f"{QB_URL}/api/v2/rss/setRule",
            data={'ruleName': rule_name, 'ruleDef': json.dumps(rule_data)},
            timeout=10
        )
        status = "включено" if enabled else "отключено"
        logging.info(f"RSS правило '{rule_name}' — {status}")
        print(f"{'✅' if enabled else '⏸️'} RSS правило '{rule_name}' — {status}")
    except Exception as e:
        logging.error(f"Ошибка изменения статуса RSS правила '{rule_name}': {e}")

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

# === ПРОВЕРКА ДИСКА И УПРАВЛЕНИЕ RSS ===
def check_disk_and_manage_rss():
    if not RSS_RULE_NAME:
        logging.warning("RSS_RULE_NAME не задан в config.ini — проверка диска пропущена")
        print("⚠️ RSS_RULE_NAME не задан в config.ini")
        return

    save_path = get_rss_rule_save_path(RSS_RULE_NAME)
    if not save_path:
        return

    disk_percent = get_disk_usage_percent(save_path)
    if disk_percent is None:
        return

    logging.info(f"Диск '{save_path}': занято {disk_percent}% (порог {DISK_THRESHOLD_PERCENT}%)")
    print(f"💾 Диск '{save_path}': {disk_percent}% занято (порог {DISK_THRESHOLD_PERCENT}%)")

    if disk_percent >= DISK_THRESHOLD_PERCENT:
        print(f"⚠️ Диск заполнен на {disk_percent}% — отключаем RSS правило '{RSS_RULE_NAME}'")
        logging.warning(f"Диск заполнен на {disk_percent}% >= {DISK_THRESHOLD_PERCENT}% — RSS отключён")
        set_rss_rule_enabled(RSS_RULE_NAME, enabled=False)
    else:
        set_rss_rule_enabled(RSS_RULE_NAME, enabled=True)

# === ОДНА ИТЕРАЦИЯ ПРОВЕРКИ ===
def run_check():
    clean_old_logs()
    logging.info("=" * 50)
    logging.info("Запуск проверки")

    try:
        login()
        r = session.get(f"{QB_URL}/api/v2/torrents/info", timeout=15)
        r.raise_for_status()
        torrents = r.json()
    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
        print(f"💥 Ошибка: {e}")
        return

    check_disk_and_manage_rss()

    full_data = load_data()
    data = full_data["torrents"]
    blacklist = full_data["blacklist"]

    blacklist = blacklist[-500:]

    now = datetime.now()
    today_str = now.date().isoformat()

    active_hashes = {t["hash"] for t in torrents}
    data = {h: v for h, v in data.items() if h in active_hashes}

    stats = {"new": 0, "deleted_error": 0, "deleted_old": 0, "blacklisted": 0, "waiting": 0, "skipped_downloading": 0}

    downloading_states = {'downloading', 'stalledDL', 'checkingDL', 'pausedDL', 'queuedDL', 'metaDL', 'forcedDL'}
    error_states = {'error', 'missingFiles', 'unknown', 'stalled_error'}

    for t in torrents:
        h = t["hash"]
        name = t["name"]
        state = t["state"]
        ratio = t.get("ratio", 0)
        leechs = t.get("num_leechs", 0)

        if state in downloading_states:
            logging.info(f"ПРОПУСК (скачивается): {name} | Статус: {state}")
            stats["skipped_downloading"] += 1
            continue

        if h in blacklist:
            delete_torrent(h, name, reason="В черном списке (ранее ошибка)")
            stats["blacklisted"] += 1
            continue

        if state in error_states:
            add_to_rss_exclude(name)
            delete_torrent(h, name, reason=f"Статус ошибки: {state}")
            if h not in blacklist:
                blacklist.append(h)
            if h in data:
                del data[h]
            stats["deleted_error"] += 1
            continue

        if h not in data:
            data[h] = {"ratio": ratio, "date": now.isoformat(), "leech_sum": leechs, "last_date": today_str}
            logging.info(f"НОВЫЙ: {name}")
            stats["new"] += 1
            continue

        if data[h].get("last_date") != today_str:
            data[h]["leech_sum"] = data[h].get("leech_sum", 0) + leechs
            data[h]["last_date"] = today_str

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

    summary = (
        f"Итоги: новых={stats['new']} | "
        f"скачиваются={stats['skipped_downloading']} | "
        f"ждут срока={stats['waiting']} | "
        f"удалено ошибок={stats['deleted_error']} | "
        f"удалено по лимиту={stats['deleted_old']} | "
        f"повторов={stats['blacklisted']}"
    )
    logging.info(summary)
    logging.info("Завершение проверки")
    print(f"\n✅ {summary}")

# === ОСНОВНОЙ ЦИКЛ ===
if __name__ == "__main__":
    setup_logger()
    logging.info("Сервисный режим запущен")
    print("🚀 QB Cleaner Server запущен")
    while True:
        run_check()
        print(f"⏰ Следующая проверка через {CHECK_INTERVAL_HOURS} ч...")
        logging.info(f"Следующая проверка через {CHECK_INTERVAL_HOURS} ч.")
        time.sleep(CHECK_INTERVAL_HOURS * 3600)
