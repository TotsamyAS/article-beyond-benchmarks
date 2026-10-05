import { cleanText } from "./utils.js";

/**
 * Определяет, содержит ли HTML признаки антибот-блокировки.
 *
 * Наличие убедительной товарной разметки имеет приоритет и отменяет
 * срабатывания на слова из скриптов. В остальных случаях проверяются CAPTCHA,
 * Cloudflare, Qrator, сообщения об ограничении доступа и характерные URL.
 *
 * @param html HTML загруженной страницы.
 * @param pageUrl Конечный URL после перенаправлений.
 * @returns Уникальные машинные маркеры блокировки; пустой список означает, что
 * блокировка не подтверждена.
 */
export function detectBlockedPage(html: string, pageUrl: string): string[] {
  if (htmlHasProductEvidence(html)) {
    return [];
  }

  const lowerHtml = html.toLowerCase();
  const lowerUrl = pageUrl.toLowerCase();
  const visible = cleanText(
    html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, " "),
  );
  const visibleLower = visible.toLowerCase();
  const markers: string[] = [];

  const add = (marker: string, condition: boolean) => {
    if (condition && !markers.includes(marker)) markers.push(marker);
  };

  add("captcha", /captcha|g-recaptcha|hcaptcha|sp_rotated_captcha/i.test(html));
  add(
    "cloudflare",
    /cloudflare|cf-ray|cdn-cgi\/challenge|__cf_chl/i.test(html),
  );
  add(
    "qrator",
    /qrator|qauth|xpvnsulc/i.test(html) || /xpvnsulc/.test(lowerUrl),
  );
  add(
    "access_denied",
    /доступ ограничен|access denied|forbidden|проблема с ip/i.test(
      visibleLower,
    ),
  );
  add(
    "ozon_rr_redirect",
    /ozon\.ru/.test(lowerUrl) &&
      /__rr=1/.test(lowerUrl) &&
      !/tile-root|tile-clickable-element|href=["']\/product\//i.test(html),
  );
  add(
    "antibot_check",
    /проверяем, что вы не робот|подозрительная активность|enable javascript/i.test(
      visibleLower,
    ),
  );

  return markers;
}

/**
 * Ищет явное сообщение о пустой поисковой выдаче.
 *
 * Проверяется только видимый текст без скриптов, причём наличие товарной
 * разметки отменяет результат, чтобы не принять служебную фразу за пустую
 * страницу.
 *
 * @param html HTML результатов поиска.
 * @returns Код найденной русской или английской формулировки либо `undefined`.
 */
export function detectEmptySearchResults(html: string): string | undefined {
  const visible = cleanText(
    html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, " "),
  );
  if (!visible) return undefined;
  if (htmlHasProductEvidence(html)) return undefined;
  const patterns: Array<[string, RegExp]> = [
    [
      "ru_no_products_matching_search",
      /нет\s+товаров[^.]{0,180}(?:поиск|запрос|критери)/i,
    ],
    [
      "ru_products_not_found",
      /(?:товары?|результаты?|предложения?)\s+не\s+найден/i,
    ],
    ["ru_nothing_found", /ничего\s+не\s+найдено/i],
    [
      "en_no_products_found",
      /no\s+(?:products?|items?|results?)\s+(?:found|available|match)/i,
    ],
  ];
  for (const [reason, pattern] of patterns) {
    if (pattern.test(visible)) return reason;
  }
  return undefined;
}

/**
 * Быстро проверяет HTML на известные признаки карточек и товарных данных.
 *
 * @param html Исходный HTML.
 * @returns `true`, если найдены селекторы, микроразметка или URL-шаблоны,
 * характерные для товаров поддерживаемых магазинов.
 */
export function htmlHasProductEvidence(html: string): boolean {
  return [
    "digi-product",
    "digi-product__price",
    "mvid-product-card",
    "current-price",
    "itemlist",
    "with-hover",
    "price-main",
    "product-item-container",
    "product-item-big-card",
    "product-list-item",
    "search-results__item",
    "js-product",
    "data-retail-price",
    "data-description",
    'data-entity="item"',
    'data-testid="product',
    "data-testid='product",
    'data-test="product',
    'data-zone-name="productSnippet"',
    'data-baobab-name="productSnippet"',
    "price_value",
    "js-productListItem",
    "listItemBuy__price",
    "product-card-list",
    "product-card",
    "product-title",
    "v-product-price__value",
    "catalog-product",
    "catalog-block-view__item",
    "item_block",
    'data-meta-name="SnippetProductVerticalLayout"',
    'data-id="product"',
    // ozon
    "tile-root",
    "tile-clickable-element",
    'href="/product/',
    "href='/product/",
    "searchResultsV2",
    "tsHeadline500Medium",
    "tsBody500Medium",

    // vseinstrumenti
    'data-qa="product-name"',
    "data-qa='product-name'",
    'data-qa="product-price-current"',
    "data-qa='product-price-current'",
    'data-qa="product-add-to-cart-button"',
    "data-qa='product-add-to-cart-button'",
    'data-qa="product-photo-click"',
    "data-qa='product-photo-click'",
    'data-qa="product-availability"',
    "data-qa='product-availability'",
    "catalog__list-item snippet",
    "promo-slider__item snippet",
    "snippet__price",
    "snippet-price__value",
    "snippet__title",
    "snippet__photo",
    "/product-",
    "itemListElement",
    '"@type":"Offer"',
    '"priceCurrency":"RUB"',
  ].some((marker) => html.includes(marker));
}
