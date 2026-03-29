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
            'STARTUP_WAIT_SEC': '10',
            'DISK_THRESHOLD_PERCENT': '90',
            'RSS_RULE_NAME': '',
            'DOWNLOAD_PROGRESS_THRESHOLD': '25'
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            config.write(f)
        print("⚙️ Создан config.ini — заполни RSS_RULE_NAME названием правила RSS")
    else:
        config.read(CONFIG_FILE, encoding='utf-8')

    settings = config['SETTINGS']

    try:
        threshold = int(settings.get('DISK_THRESHOLD_PERCENT', '90'))
        if not 1 <= threshold <= 100:
            raise ValueError
    except ValueError:
        print("❌ DISK_THRESHOLD_PERCENT должен быть от 1 до 100. Установлено 90")
        config['SETTINGS']['DISK_THRESHOLD_PERCENT'] = '90'
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            config.write(f)

    try:
        progress = int(settings.get('DOWNLOAD_PROGRESS_THRESHOLD', '25'))
        if not 1 <= progress <= 100:
            raise ValueError
    except ValueError:
        print("❌ DOWNLOAD_PROGRESS_THRESHOLD должен быть от 1 до 100. Установлено 25")
        config['SETTINGS']['DOWNLOAD_PROGRESS_THRESHOLD'] = '25'
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
STARTUP_WAIT_SEC = conf.getint('STARTUP_WAIT_SEC')
DISK_THRESHOLD_PERCENT = conf.getint('DISK_THRESHOLD_PERCENT')
RSS_RULE_NAME = conf.get('RSS_RULE_NAME', '').strip()
DOWNLOAD_PROGRESS_THRESHOLD = conf.getint('DOWNLOAD_PROGRESS_THRESHOLD')

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
        return {"torrents": {}, "blacklist": [], "paused_by_disk": []}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            content = json.load(f)
            if "torrents" not in content:
                content = {"torrents": content, "blacklist": [], "paused_by_disk": []}
            if "paused_by_disk" not in content:
                content["paused_by_disk"] = []
            return content
    except (json.JSONDecodeError, IOError):
        return {"torrents": {}, "blacklist": [], "paused_by_disk": []}

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

# === УПРАВЛЕНИЕ АВТОЗАГРУЗКОЙ RSS ===
def set_rss_autodownload(enabled: bool):
    try:
        r = session.post(
            f"{QB_URL}/api/v2/app/setPreferences",
            data={"json": json.dumps({"rss_auto_downloading_enabled": enabled})},
            timeout=10
        )
        if r.status_code == 200:
            status = "включена" if enabled else "отключена"
            logging.info(f"RSS автозагрузка — {status}")
            print(f"{'✅' if enabled else '⏸️'} RSS автозагрузка — {status}")
        else:
            logging.error(f"Ошибка изменения автозагрузки RSS: {r.status_code}")
    except Exception as e:
        logging.error(f"Ошибка изменения автозагрузки RSS: {e}")

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

# === ПАУЗА ТОРРЕНТА ===
def pause_torrent(hash_, name, reason=""):
    try:
        logging.info(f"ПАУЗА: {name} | Причина: {reason}")
        print(f"⏸️ Пауза: {name} ({reason})")
        session.post(f"{QB_URL}/api/v2/torrents/pause", data={"hashes": hash_}, timeout=10)
    except Exception as e:
        logging.error(f"Ошибка паузы {name}: {e}")

# === ВОЗОБНОВЛЕНИЕ ТОРРЕНТОВ ===
def resume_torrents(hashes: list):
    try:
        hashes_str = "|".join(hashes)
        session.post(f"{QB_URL}/api/v2/torrents/resume", data={"hashes": hashes_str}, timeout=10)
        logging.info(f"ВОЗОБНОВЛЕНО: {len(hashes)} торрентов")
        print(f"▶️ Возобновлено торрентов: {len(hashes)}")
    except Exception as e:
        logging.error(f"Ошибка возобновления торрентов: {e}")

# === ПРОВЕРКА ДИСКА И УПРАВЛЕНИЕ ЗАГРУЗКАМИ ===
def check_disk_and_manage(torrents, paused_by_disk):
    if not RSS_RULE_NAME:
        logging.warning("RSS_RULE_NAME не задан в config.ini — проверка диска пропущена")
        print("⚠️ RSS_RULE_NAME не задан в config.ini")
        return paused_by_disk

    save_path = get_rss_rule_save_path(RSS_RULE_NAME)
    if not save_path:
        return paused_by_disk

    disk_percent = get_disk_usage_percent(save_path)
    if disk_percent is None:
        return paused_by_disk

    logging.info(f"Диск '{save_path}': занято {disk_percent}% (порог {DISK_THRESHOLD_PERCENT}%)")
    print(f"💾 Диск '{save_path}': {disk_percent}% занято (порог {DISK_THRESHOLD_PERCENT}%)")

    downloading_states = {'downloading', 'stalledDL', 'checkingDL', 'queuedDL', 'metaDL', 'forcedDL'}

    if disk_percent >= DISK_THRESHOLD_PERCENT:
        logging.warning(f"Диск заполнен на {disk_percent}% — принимаем меры")
        print(f"⚠️ Диск заполнен на {disk_percent}%")

        # Отключаем RSS автозагрузку
        set_rss_autodownload(enabled=False)

        for t in torrents:
            if t["state"] not in downloading_states:
                continue

            h = t["hash"]
            name = t["name"]
            progress = t.get("progress", 0) * 100  # progress в qBit от 0.0 до 1.0

            if progress < DOWNLOAD_PROGRESS_THRESHOLD:
                # Удаляем недокачанные
                delete_torrent(h, name, reason=f"Диск забит ({disk_percent}%), прогресс {progress:.1f}%")
            else:
                # Паузим те что >= порога прогресса
                if h not in paused_by_disk:
                    pause_torrent(h, name, reason=f"Диск забит ({disk_percent}%), прогресс {progress:.1f}%")
                    paused_by_disk.append(h)

    else:
        # Место освободилось — включаем RSS и возобновляем
        set_rss_autodownload(enabled=True)

        if paused_by_disk:
            # Проверяем что торренты ещё существуют
            active_hashes = {t["hash"] for t in torrents}
            to_resume = [h for h in paused_by_disk if h in active_hashes]
            if to_resume:
                resume_torrents(to_resume)
            paused_by_disk = []

    return paused_by_disk

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
    paused_by_disk = full_data["paused_by_disk"]

    blacklist = blacklist[-500:]

    # === ПРОВЕРКА ДИСКА ===
    paused_by_disk = check_disk_and_manage(torrents, paused_by_disk)

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

    save_data({"torrents": data, "blacklist": blacklist, "paused_by_disk": paused_by_disk})

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
