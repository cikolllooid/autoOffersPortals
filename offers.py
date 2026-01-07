import time
import random
import logging
import requests
import redis

first_offer_time = None
my_balance = 0

redis_client = redis.Redis(host='localhost', port=6379, db=0)

# ---------------- CONFIG ----------------
URL_SEARCH = "https://portal-market.com/api/nfts/search"
URL_OFFER = "https://portal-market.com/api/offers/"
URL_PROFILE = "https://portal-market.com/api/users/wallets/"
URL_OFFER_PLACED = "https://portal-market.com/api/offers/placed"

TIMEOUT = 15  # ⬅️ Единый таймаут для всех запросов

HEADERS_COMMON = {
    "Host": "portal-market.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0",
    "Accept": "application/json, text/plain, */*",
    "Authorization": "YOUR auth from burpsuite",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

session_search = requests.Session()
session_search.headers.update(HEADERS_COMMON | {"Referer": "https://portal-market.com/"})

params_offers_placed = {
    "offset": 0,
    "limit": 20
}


def delete_offers():
    logging.info("🗑️  Начинаем удаление офферов...")
    try:
        response = requests.get(
            URL_OFFER_PLACED,
            headers=HEADERS_COMMON,
            params=params_offers_placed,
            timeout=TIMEOUT  # ⬅️ ДОБАВЛЕН
        )

        if response.status_code not in (200, 204):
            logging.error("Request failed: %s", response.text)
            return

        my_offers = response.json().get("offers", [])
        logging.info(f"📋 Найдено офферов для удаления: {len(my_offers)}")

        if my_offers:
            for i in my_offers:
                url = f"https://portal-market.com/api/offers/{i['id']}/cancel"
                try:
                    response = requests.post(
                        url,
                        headers=HEADERS_COMMON,
                        timeout=TIMEOUT  # ⬅️ ДОБАВЛЕН
                    )
                    time.sleep(1)

                    if response.status_code not in (200, 204):
                        logging.error("Request failed: %s", response.text)
                    else:
                        logging.info(f"✅ Оффер {i['id']} отменен")
                except requests.Timeout:
                    logging.error(f"⏱️ Таймаут при отмене оффера {i['id']}")
                except requests.RequestException as e:
                    logging.error(f"❌ Ошибка при отмене оффера {i['id']}: {e}")

    except requests.Timeout:
        logging.error("⏱️ Таймаут при получении списка офферов")
    except requests.RequestException as e:
        logging.error(f"❌ Ошибка при получении офферов: {e}")


def process_collection():
    global my_balance, first_offer_time

    params_search = {
        "offset": 0,
        "limit": 50,
        "sort_by": "listed_at desc",
        "status": "listed",
        "premarket_status": "all",
        "exclude_bundled": "true"
    }

    try:
        resp = session_search.get(URL_SEARCH, params=params_search, timeout=TIMEOUT)
        if resp.status_code != 200:
            logging.error(f"❌ Ошибка поиска: {resp.status_code}")
            return

        results = resp.json().get("results", [])
        logging.info(f"🔍 Получено NFT: {len(results)}")

        for idx, item in enumerate(results, 1):
            # Проверка таймера
            if first_offer_time is not None:
                elapsed = time.time() - first_offer_time
                logging.info(f"⏱️  Таймер: {elapsed:.1f}s / 300s")

                if elapsed > 300:
                    delete_offers()
                    first_offer_time = None
                    my_balance = 0
                    logging.info("🔄 Офферы удалены, таймер сброшен")
                    continue

            # Проверка атрибутов
            model_value = next((a["value"] for a in item.get("attributes", []) if a["type"] == "model"), None)
            model_back = next((a["value"] for a in item.get("attributes", []) if a["type"] == "backdrop"), None)

            if not model_value or not model_back:
                logging.debug(f"⏭️  NFT #{idx}: пропущен (нет атрибутов)")
                continue

            logging.info(f"📦 NFT #{idx}: {model_value} | {model_back} | Цена: {item['floor_price']}")

            time.sleep(random.uniform(2, 3))

            # Получение баланса
            if my_balance == 0:
                my_profile = redis_client.get("profile")
                if my_profile is None:
                    try:
                        response = requests.get(
                            URL_PROFILE,
                            headers=HEADERS_COMMON,
                            timeout=TIMEOUT  # ⬅️ ДОБАВЛЕН
                        )
                        user_profile = response.json()
                        redis_client.set("profile", value=user_profile["balance"], ex=10)
                        my_balance = float(user_profile["balance"])
                        logging.info(f"💰 Баланс обновлен: {my_balance}")
                    except requests.Timeout:
                        logging.error("⏱️ Таймаут при получении баланса, пропуск итерации")
                        continue
                    except requests.RequestException as e:
                        logging.error(f"❌ Ошибка при получении баланса: {e}")
                        continue
                else:
                    my_balance = float(my_profile.decode())

            # Проверка NFT в кэше
            nft_id = redis_client.get(item['id'])
            floor_price = float(item['floor_price'])

            if nft_id is not None:
                logging.info(f"⏭️  NFT #{idx}: уже обработан ранее")
                continue

            if my_balance < floor_price:
                logging.warning(f"💸 NFT #{idx}: недостаточно средств ({my_balance} < {floor_price})")
                continue

            # Размещение оффера
            if first_offer_time is None:
                first_offer_time = time.time()
                logging.info("⏱️  🚀 ТАЙМЕР ЗАПУЩЕН!")

            payload = {
                "offer": {
                    "nft_id": item['id'],
                    "offer_price": item['floor_price'],
                    "expiration_days": 7
                }
            }

            try:
                response = requests.post(
                    URL_OFFER,
                    headers=HEADERS_COMMON,
                    json=payload,
                    timeout=TIMEOUT  # ⬅️ ДОБАВЛЕН
                )

                if response.status_code == 200 or response.status_code == 204:
                    redis_client.set(name=item['id'], value='1', ex=604800)
                    my_balance -= floor_price
                    logging.info(f"✅ Оффер размещен! Остаток: {my_balance:.2f}")
                else:
                    logging.error(f"❌ Ошибка размещения оффера: {response.status_code} - {response.text}")
            except requests.Timeout:
                logging.error(f"⏱️ Таймаут при размещении оффера для NFT #{idx}")
            except requests.RequestException as e:
                logging.error(f"❌ Ошибка при размещении оффера: {e}")

    except requests.Timeout:
        logging.error("⏱️ Таймаут при поиске NFT")
    except requests.RequestException as e:
        logging.error(f"❌ Ошибка запроса: {e}")


def start_code():
    try:
        logging.info("🚀 Запуск бота...")
        while True:
            process_collection()
            time.sleep(0.1)
    except KeyboardInterrupt:
        logging.info("⛔ Программа остановлена вручную")
        redis_client.close()


if __name__ == "__main__":
    start_code()