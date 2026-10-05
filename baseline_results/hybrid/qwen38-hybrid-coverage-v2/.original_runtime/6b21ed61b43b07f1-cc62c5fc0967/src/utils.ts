import * as cheerio from "cheerio";
import type { AnyNode } from "domhandler";

/**
 * Извлекает и нормализует домен из URL или похожей строки.
 *
 * @param value URL либо доменное имя.
 * @returns Домен в нижнем регистре без префикса `www.`. При неверном URL
 * применяется осторожная строковая очистка.
 */
export function normalizeHostname(value: string): string {
  try {
    return new URL(value).hostname.toLowerCase().replace(/^www\./, "");
  } catch {
    return value
      .toLowerCase()
      .replace(/^https?:\/\//, "")
      .replace(/^www\./, "");
  }
}

/**
 * Приводит произвольное значение к однострочному видимому тексту.
 *
 * @param value Значение из DOM, JSON или запроса.
 * @returns Строка без неразрывных пробелов и повторяющихся пробельных символов.
 */
export function cleanText(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/\u00a0/g, " ")
    .replace(/\u202f/g, " ")
    .replace(/\u2009/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Очищает название товара от приклеенного JSON виджета.
 *
 * @param value Сырой текст элемента карточки.
 * @returns Нормализованное название до первого похожего на JSON блока.
 */
export function cleanProductName(value: unknown): string {
  const text = cleanText(value);
  const widgetJsonStart = text.search(/\s+\{/);
  return widgetJsonStart >= 0 ? text.slice(0, widgetJsonStart).trim() : text;
}

/**
 * Преобразует число или строку с валютой в положительную цену.
 *
 * @param value Сырое значение цены.
 * @returns Положительное число либо `null` для пустого, логического,
 * непарсируемого значения или цены «по запросу».
 */
export function coercePrice(value: unknown): number | null {
  if (typeof value === "boolean" || value === null || value === undefined) {
    return null;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return value > 0 ? value : null;
  }
  const text = cleanText(value)
    .replace(/[₽]/g, " ")
    .replace(/руб\.?/gi, " ")
    .replace(/р\./gi, " ")
    .replace(/\bр\b/gi, " ");
  if (!text || /по запросу/i.test(text)) return null;
  const match = text.match(
    /\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?/,
  );
  if (!match) return null;
  const normalized = match[0]
    .replace(/[ \u00a0\u202f\u2009]/g, "")
    .replace(",", ".")
    .replace(/\.$/, "");
  const parsed = Number.parseFloat(normalized);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

/**
 * Разрешает относительную ссылку относительно URL страницы.
 *
 * @param pageUrl Базовый URL документа.
 * @param value Сырое значение ссылки.
 * @returns Абсолютный URL, пустую строку для пустого значения либо исходный
 * текст, если конструктор URL его не принимает.
 */
export function absoluteUrl(pageUrl: string, value: unknown): string {
  const text = cleanText(value);
  if (!text) return "";
  try {
    return new URL(text, pageUrl).toString();
  } catch {
    return text;
  }
}

/**
 * Возвращает первый непустой видимый текст по списку селекторов.
 *
 * @param $ Экземпляр Cheerio текущего документа.
 * @param root Корень поиска.
 * @param selectors Селекторы в порядке приоритета.
 * @returns Найденный текст либо пустую строку.
 */
export function firstText(
  $: cheerio.CheerioAPI,
  root: cheerio.Cheerio<AnyNode>,
  selectors: string[],
): string {
  for (const selector of selectors) {
    const value = ownOrDescendantText(root.find(selector).first());
    if (value) return value;
  }
  return "";
}

/**
 * Возвращает первый непустой атрибут по списку селекторов.
 *
 * @param root Корень поиска Cheerio.
 * @param selectors Селекторы в порядке приоритета.
 * @param attrName Имя читаемого атрибута.
 * @returns Очищенное значение либо пустую строку.
 */
export function firstAttr(
  root: cheerio.Cheerio<AnyNode>,
  selectors: string[],
  attrName: string,
): string {
  for (const selector of selectors) {
    const value = cleanText(root.find(selector).first().attr(attrName));
    if (value) return value;
  }
  return "";
}

/**
 * Извлекает видимый текст узла без содержимого служебных тегов.
 *
 * @param node Узел Cheerio.
 * @returns Нормализованный текст узла и его потомков.
 */
export function ownOrDescendantText(node: cheerio.Cheerio<AnyNode>): string {
  if (node.length === 0) return "";
  const visibleNode = node.clone();
  visibleNode.find("script, style, noscript, noframes, template").remove();
  return cleanText(visibleNode.text());
}

/**
 * Эвристически определяет, ведёт ли ссылка на отдельный товар.
 *
 * Служебные страницы корзины, поиска и справки отклоняются. Принимаются
 * характерные маршруты товара, параметры с идентификатором и достаточно
 * конкретные вложенные ссылки каталога.
 *
 * @param value Сырая ссылка.
 * @returns `true` только для ссылки, похожей на карточку товара.
 */
export function looksLikeProductHref(value: string): boolean {
  const href = cleanText(value).toLowerCase();

  if (!href || href === "/" || href.startsWith("#")) return false;
  if (
    href.startsWith("javascript:") ||
    href.startsWith("mailto:") ||
    href.startsWith("tel:")
  ) {
    return false;
  }

  if (
    [
      "/cart",
      "/basket",
      "/compare",
      "/favorites",
      "/favorite",
      "/wishlist",
      "/login",
      "/auth",
      "/search",
      "/filter",
      "/delivery",
      "/payment",
      "/help",
      "/contacts",
      "/about",
      "/news",
      "/blog",
      "/reviews",
      "/review",
    ].some((part) => href.includes(part))
  ) {
    return false;
  }

  if (
    [
      "/product/",
      "/products/",
      "/goods/",
      "/good/",
      "/katalog/",
      "/shop/",
      "/item/",
      "/items/",
      "/sku/",
      "/p/",
      "/tovar/",
      "/product-",
      "/item-",
      "/goods-",
      "/offer/",
      "/offers/",
      "/books/",
    ].some((part) => href.includes(part))
  ) {
    return true;
  }

  if (/[?&](product|sku|item|offer|good|book)[_-]?id=/.test(href)) {
    return true;
  }

  // Важно: plain /catalog/1852/ — это часто категория, не товар.
  // Товарные catalog-ссылки обычно содержат product/goods/item/tovar/sku/offer
  // или имеют минимум два сегмента после /catalog/.
  if (/\/catalog\/(?:product|goods|item|tovar|sku|offer)[^?#]*/.test(href)) {
    return true;
  }

  if (
    /\/catalog\/[^?#]+\/(?:product|goods|item|tovar|sku|offer)[^?#]*/.test(href)
  ) {
    return true;
  }

  if (/\/catalog\/[^?#]+\/[^?#]*\d{3,}/.test(href)) {
    return true;
  }

  return false;
}

/**
 * Разбивает строку CSS-селекторов, разделённых запятыми.
 *
 * @param value Исходная настройка или пустое значение.
 * @returns Непустые селекторы без окружающих пробелов.
 */
export function splitSelectorList(value: string | null | undefined): string[] {
  if (!value) return [];
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

/**
 * Удаляет поддерживаемые браузером псевдоэлементы из селектора Cheerio.
 *
 * `css-select` не умеет работать с `::before` и похожими конструкциями, а их
 * сгенерированное содержимое всё равно отсутствует в HTML.
 *
 * @param selector Исходный CSS-селектор.
 * @returns Селектор, пригодный для Cheerio.
 * @throws Error Если после очистки остался неизвестный псевдоэлемент.
 */
export function normalizeCssSelectorForCheerio(selector: string): string {
  let value = selector.trim();

  // css-select не поддерживает pseudo-elements.
  // В extractor нам они не нужны: ::text/::attr обрабатываются выше,
  // ::before/::after не содержат DOM-текста.
  value = value.replace(
    /::(?:before|after|marker|placeholder|selection|first-line|first-letter)\b/gi,
    "",
  );

  // На случай, если pseudo-element остался в середине выражения.
  if (/::[a-z-]+/i.test(value)) {
    throw new Error(`Unsupported pseudo-element selector: ${selector}`);
  }

  return value.trim();
}
