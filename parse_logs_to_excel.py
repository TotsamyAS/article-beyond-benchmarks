#!/usr/bin/env python3
"""
Парсер логов PriceTracker для извлечения структурированных событий.
Принимает одну или несколько папок с логами, создаёт Excel-файл с листами по типам событий.
"""

import os
import re
import argparse
import pandas as pd
from pathlib import Path

# ----------------------------------------------------------------------
# Список шаблонов: (регулярное выражение, имя листа в Excel)
# Регулярные выражения используют именованные группы для извлечения полей.
# ----------------------------------------------------------------------
PATTERNS = [
    # --- Аналитика и поиск источников ---
    (r"analytics:start query='(?P<query>.*?)'", "Analytics_Start"),
    (r"analytics:source_exploration:start provider='(?P<provider>.*?)' query='(?P<query>.*?)' selected_sources=(?P<selected_sources>.*)", "Source_Exploration_Start"),
    (r"analytics:source_exploration:done provider='(?P<provider>.*?)' status='(?P<status>.*?)' candidate_count=(?P<candidate_count>\d+) accepted_items=(?P<accepted_items>\d+)", "Source_Exploration_Done"),
    (r"analytics:selected_sources=(?P<selected_sources>.*)", "Selected_Sources"),
    (r"analytics:registered_clients=(?P<clients>.*)", "Registered_Clients"),
    (r"analytics:client_matched url='(?P<url>.*?)' client='(?P<client>.*?)'", "Client_Matched"),
    (r"analytics:skip_source url='(?P<url>.*?)' reason=(?P<reason>.*)", "Skip_Source"),
    (r"analytics:source_search_start source='(?P<source>.*?)' url='(?P<url>.*?)'", "Source_Search_Start"),
    (r"analytics:source_search_result source='(?P<source>.*?)' parsed=(?P<parsed>\d+)", "Source_Search_Result"),
    (r"analytics:source_search_error source='(?P<source>.*?)' error=(?P<error>.*)", "Source_Search_Error"),
    (r"analytics:source_search_empty source='(?P<source>.*?)' url='(?P<url>.*?)'", "Source_Search_Empty"),
    (r"analytics:source_access_blocked source='(?P<source>.*?)' error=(?P<error>.*)", "Source_Access_Blocked"),
    (r"analytics:source_access_blocked_fallback source='(?P<source>.*?)' route='(?P<route>.*?)'", "Source_Access_Blocked_Fallback"),
    (r"analytics:failed_source_urls=(?P<urls>.*)", "Failed_Source_URLs"),
    (r"analytics:empty_source_urls=(?P<urls>.*)", "Empty_Source_URLs"),
    (r"analytics:retryable_source_urls=(?P<urls>.*)", "Retryable_Source_URLs"),
    (r"analytics:local_matches_before_search=(?P<count>\d+)", "Local_Matches_Before_Search"),
    (r"analytics:local_matches_after_search=(?P<count>\d+)", "Local_Matches_After_Search"),
    (r"analytics:collection_cache_status=(?P<status>.*?)", "Collection_Cache_Status"),
    (r"analytics:collection_cache_hits=(?P<hits>\d+)", "Collection_Cache_Hits"),
    (r"analytics:collection_cache_source_urls=(?P<urls>.*)", "Collection_Cache_Source_URLs"),
    (r"analytics:cache_evicted=(?P<count>\d+)", "Cache_Evicted"),
    (r"analytics:force_refresh=(?P<flag>.*?)", "Force_Refresh"),

    # --- Конфигурированные источники ---
    (r"configured_source:start source='(?P<source>.*?)' query='(?P<query>.*?)' search_depth=(?P<search_depth>\d+) required_fields=(?P<required_fields>.*?) optional_fields=(?P<optional_fields>.*)", "Configured_Source_Start"),
    (r"configured_source:load_page:start url='(?P<url>.*?)' page=(?P<page>\d+)", "Load_Page_Start"),
    (r"configured_source:routes=(?P<routes>.*)", "Routes"),
    (r"configured_source:selectors:start item_selector='(?P<item_selector>.*?)' field_names=(?P<field_names>.*)", "Selectors_Start"),
    (r"configured_source:selectors:rows_extracted count=(?P<count>\d+)", "Rows_Extracted"),
    (r"configured_source:products_built count=(?P<count>\d+)", "Products_Built"),
    (r"persisted_count=(?P<count>\d+) source='(?P<source>.*?)' configured=(?P<configured>.*)", "Persisted_Count"),
    (r"pipeline:field_draft name='(?P<name>.*?)' price=(?P<price>[\d.]+) product_url='(?P<product_url>.*?)'", "Field_Drafts"),
    (r"load_page:rows_preview page=(?P<page>\d+) first_name='(?P<first_name>.*?)'", "Rows_Preview"),
    (r"route_error route='(?P<route>.*?)' source_url='(?P<source_url>.*?)' error_type=(?P<error_type>.*?) error=(?P<error>.*)", "Route_Error"),
    (r"row_extraction_error page=(?P<page>\d+) route='(?P<route>.*?)' error_type=(?P<error_type>.*?) error=(?P<error>.*)", "Row_Extraction_Error"),
    (r"configured_source_search_error source='(?P<source>.*?)' error=(?P<error>.*)", "Configured_Source_Search_Error"),

    # --- LLM Fallback ---
    (r"LLM_DIAG llm_fallback:result source=(?P<source>.*?) page_url=(?P<page_url>.*?) query='(?P<query>.*?)' preflight_run_llm=(?P<preflight_run_llm>.*?) preflight_skip_reason=(?P<preflight_skip_reason>.*?) candidate_count=(?P<candidate_count>\d+) candidate_mode_tried=(?P<candidate_mode_tried>.*?) candidate_mode_succeeded=(?P<candidate_mode_succeeded>.*?) screenshot_mode_tried=(?P<screenshot_mode_tried>.*?) selected_mode=(?P<selected_mode>.*?) selected_context=(?P<selected_context>.*?) result_count=(?P<result_count>\d+)", "LLM_Fallback_Result"),
    (r"LLM_DIAG llm_fallback:empty source=(?P<source>.*?) page_url=(?P<page_url>.*?) query='(?P<query>.*?)'", "LLM_Fallback_Empty"),
    (r"LLM_DIAG llm_fallback:selected source=(?P<source>.*?) page_url=(?P<page_url>.*?) query='(?P<query>.*?)' fields=(?P<fields>.*?)", "LLM_Fallback_Selected"),

    # --- Блокировки и капчи ---
    (r"tls_check_blocked url=(?P<url>.*?) reason=(?P<reason>.*)", "TLS_Check_Blocked"),
    (r"source_access_blocked source='(?P<source>.*?)' error=(?P<error>.*)", "Access_Blocked"),
    (r"troubled:captcha_detected", "Captcha_Detected"),
    (r"troubled:captcha_solve_failed", "Captcha_Solve_Failed"),
    (r"playwright_blocked_trying_camoufox source=(?P<source>.*?)", "Playwright_Blocked"),
    (r"camoufox_failed_or_blocked source=(?P<source>.*?)", "Camoufox_Failed"),
    (r"stealth_renderer_fallback_on_block url=(?P<url>.*?)", "Stealth_Renderer_Fallback"),
    (r"stealth_renderer_fallback_failed err=(?P<err>.*)", "Stealth_Renderer_Failed"),
    (r"blocked_to_troubled_fallback source=(?P<source>.*?) url=(?P<url>.*?) reason=(?P<reason>.*)", "Blocked_To_Troubled_Fallback"),

    # --- Артефакты ---
    (r"artifact_registered run_id=(?P<run_id>.*?) source=(?P<source>.*?) backend=(?P<backend>.*?) key=(?P<key>.*?) uri=(?P<uri>.*?) content_type=(?P<content_type>.*?) size=(?P<size>\d+)", "Artifact_Registered"),

    # --- Supplier Validation ---
    (r"supplier-validation:record:result index=(?P<index>\d+) domain=(?P<domain>.*?) exit_code=(?P<exit_code>\d+) status=(?P<status>.*?) access_status=(?P<access_status>.*?) card_status=(?P<card_status>.*?) field_status=(?P<field_status>.*?) fields_found=(?P<fields_found>.*?) missing_required=(?P<missing_required>.*?) missing_preferred=(?P<missing_preferred>.*?) duration_seconds=(?P<duration_seconds>[\d.]+) reasons=(?P<reasons>.*)", "Supplier_Validation_Result"),

    # --- Card Extraction CLI ---
    (r"card-extraction-cli:result url='(?P<url>.*?)' card_found=(?P<card_found>.*?) candidate_count=(?P<candidate_count>\d+) page_url='(?P<page_url>.*?)' route=(?P<route>.*)", "Card_Extraction_Result"),

    # --- Query Relevance ---
    (r"query_relevance:record url='(?P<url>.*?)' prepared='(?P<prepared>.*?)' score=(?P<score>[\d.]+) accepted=(?P<accepted>.*?) reason=(?P<reason>.*)", "Query_Relevance_Record"),
    (r"query_relevance:kept=(?P<kept>\d+) total=(?P<total>\d+)", "Query_Relevance_Kept"),
    (r"query_relevance:error error_type=(?P<error_type>.*?) error=(?P<error>.*)", "Query_Relevance_Error"),
    (r"query_relevance:disabled reason=(?P<reason>.*)", "Query_Relevance_Disabled"),

    # --- Прочие метрики ---
    (r"average_price=(?P<average_price>[\d.]+)", "Average_Price"),
    (r"price_distribution_buckets=(?P<buckets>\d+)", "Price_Distribution_Buckets"),
]

# Компилируем регулярные выражения заранее
COMPILED_PATTERNS = [(re.compile(p), name) for p, name in PATTERNS]

# Шаблон для извлечения временной метки из начала строки
TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")


def parse_logs(input_dirs, output_excel):
    """
    Обходит папки, парсит логи и сохраняет результат в Excel.
    """
    # Словарь: имя листа -> список словарей
    data = {name: [] for _, name in PATTERNS}

    # Собираем все .log файлы
    log_files = []
    for input_dir in input_dirs:
        for root, _, files in os.walk(input_dir):
            for file in files:
                if file.endswith(".log"):
                    log_files.append(os.path.join(root, file))

    if not log_files:
        print("Не найдено .log файлов в указанных папках.")
        return

    print(f"Найдено {len(log_files)} лог-файлов. Начинаю обработку...")

    for file_path in log_files:
        print(f"  Обработка: {file_path}")
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    # Извлекаем временную метку
                    ts_match = TIMESTAMP_RE.match(line)
                    timestamp = ts_match.group(1) if ts_match else ""

                    # Проверяем каждый шаблон
                    for regex, sheet_name in COMPILED_PATTERNS:
                        match = regex.search(line)
                        if match:
                            record = match.groupdict()
                            record["timestamp"] = timestamp
                            record["log_file"] = file_path
                            data[sheet_name].append(record)
                            # Прерываемся, чтобы не дублировать одну строку в разных листах
                            break
        except Exception as e:
            print(f"    Ошибка при чтении {file_path}: {e}")

    # Запись в Excel
    print(f"Запись в Excel: {output_excel}")
    with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
        for sheet_name, records in data.items():
            if records:
                df = pd.DataFrame(records)
                # Упорядочиваем колонки: timestamp и log_file в начало
                cols = ["timestamp", "log_file"] + [c for c in df.columns if c not in ("timestamp", "log_file")]
                df = df[cols]
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                print(f"  Лист '{sheet_name}': {len(records)} записей")
            else:
                # Можно создать пустой лист с заголовками, но пропустим
                pass

    print("Готово.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Парсинг логов PriceTracker в структурированный Excel."
    )
    parser.add_argument(
        "input_dirs",
        nargs="+",
        help="Одна или несколько папок с лог-файлами (обход рекурсивный).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="parsed_logs.xlsx",
        help="Имя выходного Excel-файла (по умолчанию: parsed_logs.xlsx).",
    )
    args = parser.parse_args()

    parse_logs(args.input_dirs, args.output)