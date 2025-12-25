import time
import json
import os
from bs4 import BeautifulSoup
from openai import OpenAI
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_KEY = "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"
BASE_TASKS_URL = "https://acmp.ru/index.asp?main=tasks&str=%20&page={page}&id_type=0"
chromedriver_path = r"C:\Users\Clasti\Desktop\ACMP NEW\chromedriver.exe"
TOTAL_PAGES = 20
JSON_FILE = "acmp_solutions.json"
WRONG_SOLUTIONS_FILE = "acmp_wrong_solutions.json"

options = Options()
options.add_argument("--start-maximized")
options.add_argument("--disable-extensions")
options.add_argument("--disable-gpu")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option('useAutomationExtension', False)

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=API_KEY)

def load_solutions():
    """Загружает ранее принятые решения из JSON-файла."""
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_solutions(solutions):
    """Сохраняет принятые решения в JSON-файл с сортировкой по ID задач."""
    sorted_items = sorted(solutions.items(), key=lambda x: int(x[0]))
    with open(JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump({str(k): v for k, v in sorted_items}, f, ensure_ascii=False, indent=2)


def load_wrong_solutions():
    """Загружает неправильные решения из JSON-файла для последующих исправлений."""
    if os.path.exists(WRONG_SOLUTIONS_FILE):
        with open(WRONG_SOLUTIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_wrong_solutions(wrong_solutions):
    """Сохраняет неправильные решения в JSON-файл с сортировкой по ID задач."""
    sorted_items = sorted(wrong_solutions.items(), key=lambda x: int(x[0]))
    with open(WRONG_SOLUTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump({str(k): v for k, v in sorted_items}, f, ensure_ascii=False, indent=2)

def get_task_id_from_url(url):
    """Извлекает ID задачи из URL-адреса."""
    import re
    match = re.search(r'id_task=(\d+)', url)
    return int(match.group(1)) if match else None


def clean_code(text: str) -> str:
    """Удаляет markdown-обёртку из сгенерированного кода."""
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).rstrip()


def safe_get_page(driver, url, timeout=10):
    """Пытается открыть страницу с повторными попытками при ошибке."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            driver.get(url)
            time.sleep(1)
            return True
        except:
            time.sleep(2)
    return False


def safe_find_element(driver, by, value, timeout=3):
    """Безопасно ищет элемент на странице, возвращает None при отсутствии."""
    try:
        wait = WebDriverWait(driver, timeout)
        return wait.until(EC.presence_of_element_located((by, value)))
    except:
        return None

def fetch_task_details(driver):
    """
    Извлекает из текущей страницы условие, входные/выходные данные и примеры задачи.
    Возвращает словарь с ключами: condition, input, output, examples.
    """
    try:
        soup = BeautifulSoup(driver.page_source, "lxml")
        h1_tag = soup.find("h1")
        if h1_tag:
            condition_parts = []
            current = h1_tag.find_next_sibling()
            while current:
                if current.name == "h2" and current.get_text(strip=True) in ["Входные данные", "Выходные данные", "Пример", "Пояснение к примеру"]:
                    break
                if current.name == "p" and "text" in current.get("class", []):
                    condition_parts.append(current.get_text(strip=True))
                current = current.find_next_sibling()
            main_condition = " ".join(condition_parts)
        else:
            main_condition = ""
            for p in soup.find_all("p", class_="text"):
                if p.find_previous("h2", string="Входные данные"):
                    continue
                main_condition += p.get_text(strip=True) + " "
            main_condition = main_condition.strip()

        input_data = ""
        input_section = soup.find("h2", string="Входные данные")
        if input_section:
            next_p = input_section.find_next("p", class_="text")
            if next_p:
                input_data = next_p.get_text(strip=True)

        output_data = ""
        output_section = soup.find("h2", string="Выходные данные")
        if output_section:
            next_p = output_section.find_next("p", class_="text")
            if next_p:
                output_data = next_p.get_text(strip=True)

        examples = []
        example_section = soup.find("h2", string="Пример")
        if example_section:
            table = example_section.find_next("table", class_="main")
            if table:
                rows = table.find_all("tr")[1:]
                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) >= 3:
                        try:
                            examples.append({
                                "num": cells[0].get_text(strip=True),
                                "input": cells[1].get_text(strip=True),
                                "output": cells[2].get_text(strip=True)
                            })
                        except:
                            continue

        return {"condition": main_condition, "input": input_data, "output": output_data, "examples": examples}
    except:
        return {"condition": "", "input": "", "output": "", "examples": []}

def generate_solution(task_details: dict, previous_code: str = None, error: str = None):
    """Генерирует решение задачи учитывая предыдущий код и ошибку при повторных попытках."""
    condition = task_details.get("condition", "")
    input_desc = task_details.get("input", "")
    output_desc = task_details.get("output", "")
    examples = task_details.get("examples", [])

    if previous_code is None:
        prompt = (
            "Ты — эксперт Python. Всегда отвечай только готовым кодом на Python без любых пояснений, комментариев (кроме кода), описаний или форматирования. Не приветствуй, не объясняй логику, не комментируй решения.\n\n"
            "Формат ответа:\n"
            "1. Только код на Python\n"
            "2. Никакого текста до или после кода\n\n"
            "Теперь решай:\n"
            f"Условие задачи:\n{condition}\n\n"
            f"Входные данные:\n{input_desc}\n\n"
            f"Выходные данные:\n{output_desc}\n\n"
        )
        if examples:
            prompt += "Примеры:\n"
            for ex in examples:
                prompt += f"Пример {ex['num']}:\n  Вход: {ex['input']}\n  Выход: {ex['output']}\n"
    else:
        prompt = (
            "Ты — эксперт Python. Ты ранее отправил неправильное решение. Исправь его, учитывая ошибку. Всегда отвечай только готовым кодом на Python без любых пояснений, комментариев, описаний или форматирования.\n\n"
            "Формат ответа:\n"
            "1. Только код на Python\n"
            "2. Никакого текста до или после кода\n\n"
            f"Условие задачи:\n{condition}\n\n"
            f"Входные данные:\n{input_desc}\n\n"
            f"Выходные данные:\n{output_desc}\n\n"
        )
        if examples:
            prompt += "Примеры:\n"
            for ex in examples:
                prompt += f"Пример {ex['num']}:\n  Вход: {ex['input']}\n  Выход: {ex['output']}\n"
        prompt += f"\nПредыдущий код:\n{previous_code}\n\nОшибка:\n{error}\n"

    print(f"\n--- Промпт ---\n{prompt}\n--- Конец промпта ---\n")
    
    try:
        completion = client.chat.completions.create(
            model="kwaipilot/kat-coder-pro:free",
            messages=[{"role": "user", "content": prompt}],
            timeout=30
        )
        return clean_code(completion.choices[0].message.content)
    except Exception as e:
        return f"[Ошибка API: {e}]"

# Инициализация браузера
service = Service(executable_path=chromedriver_path)
driver = webdriver.Chrome(service=service, options=options)
wait = WebDriverWait(driver, 10)

# Вход вручную
driver.get("https://acmp.ru/")
print("Пожалуйста, войдите в аккаунт вручную в открывшемся окне браузера.")
input("Когда войдёте — нажмите ENTER здесь, чтобы запустить автоматическое решение...")

# Загрузка уже решённых задач
solutions = load_solutions()

# Проход по всем страницам задач
for page in range(TOTAL_PAGES):
    print(f"\n--- Обработка страницы {page + 1}/{TOTAL_PAGES} ---")
    tasks_url = BASE_TASKS_URL.format(page=page)

    if not safe_get_page(driver, tasks_url):
        print(f"Не удалось загрузить страницу {page}, пропускаем...")
        continue

    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.main")))
    except:
        continue

    soup = BeautifulSoup(driver.page_source, "lxml")
    table = soup.find("table", class_="main")
    if not table:
        continue

    task_urls = []
    rows = table.find_all("tr", class_="white")
    for row in rows:
        tds = row.find_all("td")
        if len(tds) < 3:
            continue
        task_link = tds[2].find("a", href=True)
        if task_link and "main=task" in task_link["href"] and "id_task=" in task_link["href"]:
            href = task_link["href"]
            full_url = "https://acmp.ru/" + href.lstrip("/")
            task_urls.append(full_url)

    if not task_urls:
        continue

    print(f"Найдено задач: {len(task_urls)}")

    # Обработка каждой задачи
    for task_url in task_urls:
        task_id = get_task_id_from_url(task_url)
        if task_id is None:
            print(f"Не удалось получить ID задачи для URL: {task_url}")
            continue

        print(f"\nОбработка задачи ID {task_id}: {task_url}")

        # Пропуск уже решённых
        if str(task_id) in solutions:
            print(f"✅ Решение уже принято. Пропускаем.")
            continue

        # Получение полного условия задачи
        task_details = None
        for parse_attempt in range(5):
            if not safe_get_page(driver, task_url):
                continue
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "p.text")))
            except:
                continue
            task_details = fetch_task_details(driver)
            if task_details["condition"]:
                break
            time.sleep(1)

        if not task_details or not task_details["condition"]:
            print("Не удалось получить условие задачи, пропускаем...")
            continue

        # Загрузка последнего неправильного решения (если есть)
        wrong_solutions = load_wrong_solutions()
        previous_wrong = wrong_solutions.get(str(task_id))

        accepted = False
        last_error = None

        # Три попытки решения
        for attempt in range(1, 4):
            print(f"\n--- Попытка {attempt}/3 для задачи {task_id} ---")

            if attempt == 1 and previous_wrong:
                print(f"Генерация исправления на основе сохранённого кода. Ошибка: {previous_wrong['error']}")
                current_code = generate_solution(
                    task_details,
                    previous_code=previous_wrong["code"],
                    error=previous_wrong["error"]
                )
            elif attempt > 1:
                print(f"Генерация исправления. Ошибка: {last_error}")
                current_code = generate_solution(
                    task_details,
                    previous_code=current_code,
                    error=last_error
                )
            else:
                print("Генерация нового решения...")
                current_code = generate_solution(task_details)

            if current_code.startswith("[Ошибка API"):
                print("Ошибка API, пропускаем задачу.")
                break

            time.sleep(10)
            print("Ожидание 10 секунд перед отправкой...")
            time.sleep(10)

            # Отправка решения на сайт
            max_submit_retries = 3
            submit_success = False
            for _ in range(max_submit_retries):
                if "id_task=" not in driver.current_url:
                    if not safe_get_page(driver, task_url):
                        break

                textarea = safe_find_element(driver, By.ID, "source")
                if textarea:
                    driver.execute_script("arguments[0].value = '';", textarea)
                    driver.execute_script("arguments[0].value = arguments[1];", textarea, current_code)
                    driver.execute_script("Editor.setValue(arguments[0]);", current_code)

                    form = safe_find_element(driver, By.XPATH, "//form[contains(@action, 'main=update')]")
                    if form:
                        try:
                            driver.execute_script("arguments[0].submit();", form)
                            submit_success = True
                            time.sleep(2)
                            break
                        except:
                            pass
                    else:
                        time.sleep(1)
                else:
                    time.sleep(1)

                driver.refresh()
                time.sleep(2)

            if not submit_success:
                last_error = "Не удалось отправить решение"
                print(last_error)
                
                wrong_solutions[str(task_id)] = {
                    "code": current_code,
                    "error": last_error,
                    "timestamp": time.time()
                }
                save_wrong_solutions(wrong_solutions)
                continue

            # Проверка результата выполнения
            print("Ожидание проверки (5 секунд)...")
            time.sleep(5)

            if not safe_get_page(driver, "https://acmp.ru/index.asp?main=status"):
                last_error = "Не удалось открыть страницу статуса"
                print(last_error)
                wrong_solutions[str(task_id)] = {
                    "code": current_code,
                    "error": last_error,
                    "timestamp": time.time()
                }
                save_wrong_solutions(wrong_solutions)
                continue

            time.sleep(2)
            soup_status = BeautifulSoup(driver.page_source, "lxml")
            table = soup_status.find("table", class_="main refresh")
            if not table:
                last_error = "Таблица статуса не загружена"
                print(last_error)
                wrong_solutions[str(task_id)] = {
                    "code": current_code,
                    "error": last_error,
                    "timestamp": time.time()
                }
                save_wrong_solutions(wrong_solutions)
                continue

            found = False
            last_error = "Ваша попытка не найдена"
            rows = table.find_all("tr", class_=["gray", "white", "lightgreen"])
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 7:
                    continue
                author_cell = cells[2].get_text(strip=True)
                result_cell = cells[5]
                if "Галимов Илья Тимурович" in author_cell:
                    result_text = result_cell.get_text(strip=True)
                    found = True
                    if "Accepted" in result_text:
                        print("✅ Принято!")
                        solutions[str(task_id)] = current_code
                        save_solutions(solutions)
                        if str(task_id) in wrong_solutions:
                            del wrong_solutions[str(task_id)]
                            save_wrong_solutions(wrong_solutions)
                        accepted = True
                    else:
                        last_error = result_text
                        print(f"❌ Ошибка: {last_error}")
                    break

            if not found:
                print(last_error)

            # Сохранение неправильного решения
            if not accepted:
                wrong_solutions[str(task_id)] = {
                    "code": current_code,
                    "error": last_error,
                    "timestamp": time.time()
                }
                save_wrong_solutions(wrong_solutions)
                print(f"Неправильное решение сохранено после попытки {attempt}.")

            if accepted:
                break

        if not accepted:
            print(f"Задача {task_id} не решена за 3 попытки. Переход к следующей...")

print("\n✅ Обработка всех страниц завершена.")
driver.quit()
