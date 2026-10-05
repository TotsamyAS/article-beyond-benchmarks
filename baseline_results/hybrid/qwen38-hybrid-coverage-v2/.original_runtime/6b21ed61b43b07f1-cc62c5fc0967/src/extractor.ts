import * as cheerio from "cheerio";
import type { AnyNode } from "domhandler";

import {
  detectBlockedPage,
  detectEmptySearchResults,
} from "./blockDetection.js";
import type {
  ExtractProductsRequest,
  ExtractProductsResult,
  ProductRow,
  SearchProduct,
  SupplierProfile,
} from "./contracts.js";
import { getSupplierProfile } from "./profiles.js";
import {
  absoluteUrl,
  cleanText,
  coercePrice,
  firstAttr,
  firstText,
  looksLikeProductHref,
  normalizeHostname,
  ownOrDescendantText,
  splitSelectorList,
  cleanProductName,
  normalizeCssSelectorForCheerio,
} from "./utils.js";
import { discoverProductCandidates } from "./candidates.js";
import { filterRowsByQueryRelevance } from "./queryRelevance.js";

// Селекторы расположены от специфичных и устойчивых к наиболее широким:
// раннее совпадение уменьшает риск принять общий элемент списка за товар.
const genericSelectors = [
  "a[mvid-product-card]",
  "tr.with-hover",
  ".itemlist tr",
  "[data-id='product']",
  "[data-meta-product-id]",
  "[itemtype*='Product']",
  ".search-results__item",
  ".js-product",
  ".product-item-container",
  ".product-item-big-card",
  ".product-list-item",

  // Repeated generic ecommerce cards.
  ".catalog__list-item.snippet",
  ".catalog-product",
  "[class*='catalog-product' i]",
  ".catalog__list-item.snippet",
  ".promo-slider__item.snippet",
  ".promo-slider__item.snippet",
  ".snippet",
  ".snippet__content",
  "[class~='snippet']",

  ".product-card-list",
  ".product-card",
  "[class*='product-card__']",

  ".catalog-product",
  "[class*='catalog-product' i]",
  ".catalog-item",
  ".catalog_item",
  ".item_block",
  ".product-item",

  // Broad selectors last.
  "article",
  "li",

  "a[data-qa='product-name']",
  "[data-qa='product-price-current']",
  "a[data-qa='product-photo-click']",
  "[data-qa='product-add-to-cart-button']",
];

/**
 * Извлекает товарные строки по явным селекторам полей.
 *
 * Селекторы полей берутся по приоритету: из запроса, из конфигурации source,
 * затем из профиля поставщика. Контейнеры карточек выбираются в том же порядке:
 * request.itemSelector, source.itemSelector, profile.itemSelector, затем body.
 * Это позволяет профильным пайплайнам извлекать товары даже тогда, когда API
 * не передал fieldSelectors вместе с source.
 * Из каждой карточки извлекаются обязательные название, цена и ссылка на товар.
 * Дополнительно, если селекторы доступны, подтягиваются изображение, доставка
 * и признак наличия. Строки без имени, цены или ссылки не возвращаются.
 *
 * @param $ Cheerio-документ уже загруженной HTML-страницы.
 * @param request URL страницы, source, явные селекторы и контекст извлечения.
 * @param profile Профиль поставщика с резервными itemSelector и fieldSelectors.
 * @returns Нормализованные товарные строки, найденные по селекторам.
 */
export function extractProductsFromHtml(
  request: ExtractProductsRequest,
): ExtractProductsResult {
  const limit = Math.max(1, Math.min(500, request.limit ?? 48));
  const sourceUrl = request.source?.url ?? request.pageUrl;
  const profile = getSupplierProfile(request.pageUrl, sourceUrl);
  const route = profile
    ? `pipeline:${profile.domain}`
    : `pipeline:generic:${normalizeHostname(sourceUrl) || "unknown"}`;
  const debug: string[] = [`route=${route}`];
  const blockedMarkers = detectBlockedPage(request.html, request.pageUrl);
  if (blockedMarkers.length > 0) {
    return buildResult({
      status: "blocked",
      route,
      pageUrl: request.pageUrl,
      rows: [],
      blockedMarkers,
      debug: [...debug, `blocked=${blockedMarkers.join(",")}`],
    });
  }

  const $ = cheerio.load(request.html);

  const selectorRows = extractRows($, request, profile, limit);

  const shouldDiscoverCandidates =
    selectorRows.length === 0 || request.includeCandidates === true;

  debug.push(
    shouldDiscoverCandidates
      ? selectorRows.length === 0
        ? "candidate_discovery=enabled_no_selector_rows"
        : "candidate_discovery=enabled_for_onboarding_learning"
      : "candidate_discovery=skipped_selector_rows_found",
  );

  const candidates = shouldDiscoverCandidates
    ? discoverProductCandidates({
        $,
        pageUrl: request.pageUrl,
        profile,
        itemSelector: request.itemSelector ?? request.source?.itemSelector,
        limit: Math.max(limit, 24),
      })
    : [];

  // Важно: candidates используются как fallback rows только если быстрый selector path пустой.
  // Иначе onboarding может использовать candidates для обучения selectors,
  // но collection не получит ложные rows из candidate discovery.
  const candidateRows =
    selectorRows.length === 0
      ? candidates
          .filter((candidate) => candidate.missingFields.length === 0)
          .map((candidate) => candidate.fields as ProductRow)
      : [];

  const extractedRows = uniqueRows([...selectorRows, ...candidateRows]).slice(
    0,
    limit,
  );

  const relevance = filterRowsByQueryRelevance(extractedRows, request.query);

  const rows = relevance.rows;
  const emptyReason =
    rows.length === 0 ? detectEmptySearchResults(request.html) : undefined;
  const products = buildProductsFromRows(rows);
  const rejectedRows = countRejectedRows(rows);

  const rejectReasons: Record<string, number> = {};
  for (const candidate of candidates) {
    if (!candidate.rejectReason) continue;
    rejectReasons[candidate.rejectReason] =
      (rejectReasons[candidate.rejectReason] ?? 0) + 1;
  }

  return {
    status: products.length > 0 ? "ok" : "empty",
    route,
    pageUrl: request.pageUrl,
    queryTags: relevance.queryTags,
    rows,
    products,
    diagnostics: {
      blockedMarkers,
      emptyReason,
      candidateCount: candidates.length,
      rowCount: products.length,
      rejectedRows,
      queryRelevance: relevance.diagnostics,
      debug: [
        ...debug,
        `selector_rows=${selectorRows.length}`,
        `candidates=${candidates.length}`,
        `rows_before_query_relevance=${extractedRows.length}`,
        `rows_after_query_relevance=${rows.length}`,
        `query_tags=${relevance.queryTags.map((tag) => tag.value).join("|")}`,
        `query_relevance_rejected=${relevance.diagnostics.rejectedCount}`,
        `products=${products.length}`,
        ...(emptyReason ? [`empty_reason=${emptyReason}`] : []),
      ],
      candidates: request.includeCandidates
        ? {
            candidateCount: candidates.length,
            validRowCount: rows.length,
            rejectedCandidateCount: candidates.filter(
              (candidate) => candidate.rejectReason,
            ).length,
            rejectReasons,
            topCandidates: candidates.slice(0, 12),
          }
        : undefined,
    },
  };
}

/**
 * Получает товарные строки быстрыми селекторными и JSON-маршрутами.
 *
 * @param $ Документ Cheerio.
 * @param request Параметры извлечения.
 * @param profile Профиль известного поставщика.
 * @param limit Максимальное число строк.
 * @returns Уникальные строки в пределах лимита.
 */
function extractRows(
  $: cheerio.CheerioAPI,
  request: ExtractProductsRequest,
  profile: SupplierProfile | undefined,
  limit: number,
): ProductRow[] {
  let explicitRows: ProductRow[] = [];
  try {
    explicitRows = extractRowsWithSelectors($, request, profile);
  } catch {
    explicitRows = [];
  }
  if (explicitRows.length > 0) return uniqueRows(explicitRows).slice(0, limit);

  const selectors = [
    ...splitSelectorList(request.itemSelector),
    ...splitSelectorList(request.source?.itemSelector),
    ...splitSelectorList(profile?.itemSelector),
    ...genericSelectors,
  ];
  const rows: ProductRow[] = [];
  const seenNodes = new Set<AnyNode>();

  for (const selector of selectors) {
    let nodes: cheerio.Cheerio<AnyNode>;
    try {
      nodes = $(selector);
    } catch {
      continue;
    }
    nodes.each((_, element) => {
      if (rows.length >= limit) return false;
      if (seenNodes.has(element)) return;
      seenNodes.add(element);
      const node = $(element);
      const row = rowFromNode($, node, request.pageUrl, profile);
      if (row) rows.push(row);
      return undefined;
    });
    if (
      rows.length >= Math.min(4, limit) &&
      selector === profile?.itemSelector
    ) {
      break;
    }
  }

  if (rows.length === 0) {
    rows.push(
      ...extractRowsFromEmbeddedJson(request.html, request.pageUrl, profile),
    );
  }

  return uniqueRows(rows).slice(0, limit);
}

/**
 * Извлекает строки строго по селекторам, сохранённым для источника.
 *
 * @param $ Документ Cheerio.
 * @param request Запрос с item- и field-селекторами.
 * @returns Строки, в которых одновременно найдены имя, цена и URL.
 */
function extractRowsWithSelectors(
  $: cheerio.CheerioAPI,
  request: ExtractProductsRequest,
  profile?: SupplierProfile,
): ProductRow[] {
  const fieldSelectors =
    request.fieldSelectors ??
    request.source?.fieldSelectors ??
    profile?.fieldSelectors;
  if (!fieldSelectors || Object.keys(fieldSelectors).length === 0) return [];
  const itemSelectors = splitSelectorList(
    request.itemSelector ??
      request.source?.itemSelector ??
      profile?.itemSelector ??
      "body",
  );
  const roots = itemSelectors.length > 0 ? itemSelectors : ["body"];
  const rows: ProductRow[] = [];

  for (const selector of roots) {
    let rootNodes: cheerio.Cheerio<AnyNode>;

    try {
      rootNodes = $(normalizeCssSelectorForCheerio(selector));
    } catch {
      continue;
    }

    rootNodes.each((_, element) => {
      const root = $(element);
      const fields: Record<string, string> = {};
      for (const [fieldName, expression] of Object.entries(fieldSelectors)) {
        const value = extractSelectorExpression(
          $,
          root,
          expression,
          request.pageUrl,
        );
        if (value) fields[fieldName] = value;
      }
      const price = coercePrice(fields.price ?? fields.old_price);
      const name = cleanProductName(fields.name);
      const productUrl = absoluteUrl(
        request.pageUrl,
        fields.product_url ?? fields.url,
      );
      if (name && price && productUrl) {
        const row: ProductRow = { name, price, product_url: productUrl };

        const imageUrl = absoluteUrl(
          request.pageUrl,
          fields.image_url?.split(/\s+/, 1)[0],
        );
        if (imageUrl) row.image_url = imageUrl;

        const deliveryTime = cleanText(fields.delivery_time);
        if (deliveryTime) row.delivery_time = deliveryTime.slice(0, 180);

        const availabilityText = cleanText(
          fields.availability ?? fields.delivery_time,
        ).toLowerCase();

        if (
          /нет в наличии|нет на складе|нет в продаже|недоступен/.test(
            availabilityText,
          )
        ) {
          row.in_stock = false;
        } else if (
          /есть в наличии|в наличии|доступны|доступно|купить|в корзину/.test(
            availabilityText,
          )
        ) {
          row.in_stock = true;
        }

        rows.push(row);
      }
    });
  }
  return rows;
}

/**
 * Выполняет выражение поля относительно одной карточки.
 *
 * Поддерживаются обычный текст, `::text`, `::attr(name)` и сокращение
 * `selector@attribute`; несколько альтернатив проверяются по порядку.
 *
 * @param $ Документ Cheerio.
 * @param root Корень карточки.
 * @param expression Выражение селектора.
 * @param pageUrl Базовый URL для ссылок и изображений.
 * @returns Первое непустое значение либо пустую строку.
 */
function extractSelectorExpression(
  $: cheerio.CheerioAPI,
  root: cheerio.Cheerio<AnyNode>,
  expression: string,
  pageUrl: string,
): string {
  for (const rawPart of expression
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)) {
    const { selector, attr } = parseSelectorExpression(rawPart);
    if (!selector) continue;

    let target: cheerio.Cheerio<AnyNode>;
    try {
      target = root.find(selector).first();
    } catch {
      continue;
    }

    if (target.length === 0) continue;

    const raw = attr ? target.attr(attr) : target.text();
    const value = cleanText(raw);
    if (!value) continue;

    return attr === "href" || attr === "src"
      ? absoluteUrl(pageUrl, value)
      : value;
  }

  return "";
}

/**
 * Разбирает расширенное выражение селектора на CSS и имя атрибута.
 *
 * @param expression Настройка поля источника.
 * @returns Нормализованный CSS-селектор и пустой атрибут для текста либо имя
 * читаемого атрибута.
 */
function parseSelectorExpression(expression: string): {
  selector: string;
  attr: string;
} {
  let value = expression.trim();

  const attrPseudoMatch = value.match(/::attr\(([^)]+)\)\s*$/i);
  if (attrPseudoMatch) {
    value = value.slice(0, attrPseudoMatch.index).trim();
    return {
      selector: normalizeCssSelectorForCheerio(value),
      attr: attrPseudoMatch[1]?.trim() ?? "",
    };
  }

  value = value.replace(/::text(?:\(\))?\s*$/i, "").trim();

  const atMatch = value.match(
    /^(?<selector>.+?)@(?<attr>[A-Za-z_:][-A-Za-z0-9_:.]*)$/,
  );
  if (atMatch?.groups) {
    return {
      selector: normalizeCssSelectorForCheerio(atMatch.groups.selector.trim()),
      attr: atMatch.groups.attr.trim(),
    };
  }

  return {
    selector: normalizeCssSelectorForCheerio(value),
    attr: "",
  };
}

/**
 * Отбрасывает строку, похожую на рейтинг, рекламу или элемент интерфейса.
 *
 * @param node Предполагаемая карточка.
 * @param name Извлечённое название.
 * @param price Извлечённая цена.
 * @returns `true`, если сочетание классов и текста не похоже на товар.
 */
function isSuspiciousNonProductRow(
  node: cheerio.Cheerio<AnyNode>,
  name: string,
  price: number,
): boolean {
  const className = cleanText(node.attr("class")).toLowerCase();
  const zoneName = cleanText(node.attr("data-zone-name")).toLowerCase();
  const normalizedName = cleanText(name).toLowerCase();
  const text = ownOrDescendantText(node).toLowerCase();

  const isRealCatalogRoot =
    node.hasClass("catalog-product") ||
    node.hasClass("snippet") ||
    node.hasClass("product-card") ||
    zoneName === "productsnippet" ||
    Boolean(node.attr("data-id")) ||
    Boolean(node.attr("data-product-id")) ||
    Boolean(node.attr("data-offer-id"));

  if (
    /help-choosing|help|banner|promo|sidebar|aside|filter|pagination|breadcrumb/.test(
      className,
    )
  ) {
    return true;
  }

  if (
    /затрудняетесь в выборе|помогут вам определиться|материалы, которые помогут/.test(
      text,
    )
  ) {
    return true;
  }

  if (
    !isRealCatalogRoot &&
    /rating|comment|review|opinion|question|stat|compare|wishlist|pagination|avail/.test(
      className,
    )
  ) {
    return true;
  }

  if (
    /^\d+(?:[.,]\d+)?(?:\s*\|\s*[\d.,kк]+\s*отзывов?)?$/.test(normalizedName)
  ) {
    return true;
  }

  if (
    price < 50 &&
    /отзыв|вопрос|коммент|рейтинг|надежность|сравнить|выбор/.test(
      normalizedName,
    )
  ) {
    return true;
  }

  return false;
}

/**
 * Строит товарную строку из одного найденного DOM-узла.
 *
 * @param $ Документ Cheerio.
 * @param node Исходный узел.
 * @param pageUrl Базовый URL.
 * @param profile Профиль поставщика.
 * @returns Полную строку с обязательными полями либо `null`.
 */
function rowFromNode(
  $: cheerio.CheerioAPI,
  node: cheerio.Cheerio<AnyNode>,
  pageUrl: string,
  profile: SupplierProfile | undefined,
): ProductRow | null {
  const root = expandToLikelyProductContainer($, node, profile);

  const link = bestProductLink($, root, profile);
  const productUrl = link ? absoluteUrl(pageUrl, link.attr("href")) : "";
  const name = extractName($, root, link);
  const price = extractPrice($, root);
  if (!name || price === null || !productUrl) return null;
  if (isSuspiciousNonProductRow(root, name, price)) return null;

  const row: ProductRow = {
    name,
    price,
    product_url: productUrl,
  };

  const imageUrl = extractImageUrl(root, pageUrl);
  if (imageUrl) row.image_url = imageUrl;

  const deliveryTime = firstText($, root, [
    ".delivery-info-widget",
    ".delivery-info-widget__text",
    ".order-avail-wrap_main",
    ".catalog-product__avails",
    "[data-auto='delivery-wrapper']",
    "[class*='delivery']",
    "[class*='availability']",
    ".scenario-availability-item",
    "[data-qa='product-availability']",
  ]);
  if (deliveryTime) row.delivery_time = deliveryTime;

  const stock = extractStock(root);
  if (stock !== undefined) row.in_stock = stock;

  return row;
}

/**
 * Выбирает наиболее вероятную товарную ссылку внутри карточки.
 *
 * @param $ Документ Cheerio.
 * @param node Корень карточки.
 * @param profile Профиль с подсказками URL.
 * @returns Лучший элемент `<a>` либо `null`.
 */
function bestProductLink(
  $: cheerio.CheerioAPI,
  node: cheerio.Cheerio<AnyNode>,
  profile: SupplierProfile | undefined,
): cheerio.Cheerio<AnyNode> | null {
  let best: AnyNode | null = null;
  let bestScore = Number.NEGATIVE_INFINITY;
  const links = node.is("a[href]")
    ? node.add(node.find("a[href]"))
    : node.find("a[href]");
  links.each((_, element) => {
    const link = $(element);
    const href = cleanText(link.attr("href"));
    const absolute = absoluteUrl("https://example.test", href).toLowerCase();
    const hints = profile?.productUrlHints ?? [];
    if (
      !looksLikeProductHref(href) &&
      !hints.some((hint) => absolute.includes(hint))
    ) {
      return;
    }
    const label =
      ownOrDescendantText(link) ||
      cleanText(link.attr("title")) ||
      cleanText(link.attr("aria-label"));
    let score = 1;
    if (label.length >= 8) score += 2;
    if (hints.some((hint) => absolute.includes(hint))) score += 3;
    if (cleanText(link.attr("data-auto")) === "snippet-link") score += 4;
    if (cleanText(link.parent().attr("data-zone-name")) === "title") score += 2;
    if (/title|name|product/i.test(cleanText(link.attr("class")))) score += 2;
    if (score > bestScore) {
      bestScore = score;
      best = element;
    }
  });
  return best ? $(best) : null;
}

/**
 * Извлекает и очищает название товара из наиболее надёжного источника.
 *
 * @param $ Документ Cheerio.
 * @param node Корень карточки.
 * @param link Уже выбранная товарная ссылка.
 * @returns Название длиной не более 240 символов.
 */
function extractName(
  $: cheerio.CheerioAPI,
  node: cheerio.Cheerio<AnyNode>,
  link: cheerio.Cheerio<AnyNode> | null,
): string {
  const fromSpecificSelectors = firstText($, node, [
    // Stable product-title selectors first.
    "a.catalog-product__name",
    ".catalog-product__name",
    "[data-auto='snippet-title']",
    "a[data-qa='product-name']",
    "[data-qa='product-name']",

    // Generic snippet cards.
    "[itemprop='name']",
    "a.catalog-product__name",
    ".catalog-product__name",
    ".snippet__title",
    ".snippet__title span",
    "a.snippet__title",
    "a.snippet__title span",
    ".snippet-title",
    ".snippet-title span",

    // Generic product-card cards.
    ".product-card__name",
    "a.product-card__name",
    ".product-card__title",
    "a.product-card__title",
  ]);

  const fromGenericSelectors = firstText($, node, [
    // Generic selectors only after product-specific selectors.
    "[class*='product'][class*='title']",
    "[class*='product'][class*='name']",
    "[class*='goods'][class*='title']",
    "[class*='goods'][class*='name']",
  ]);

  const value =
    fromSpecificSelectors ||
    fromGenericSelectors ||
    cleanText(link?.attr("title")) ||
    cleanText(link?.attr("aria-label")) ||
    cleanText(node.attr("data-description")) ||
    (link ? ownOrDescendantText(link) : "") ||
    firstAttr(node, ["img[alt]"], "alt");

  return cleanProductName(value)
    .replace(/^Открыть карточку товара\s+/i, "")
    .replace(/^(купить|в корзину|подробнее)\s+/i, "")
    .slice(0, 240);
}

/**
 * Извлекает текущую цену, избегая старых цен, кредита и артикула.
 *
 * @param $ Документ Cheerio.
 * @param node Корень карточки.
 * @returns Положительную цену либо `null`.
 */
function extractPrice(
  $: cheerio.CheerioAPI,
  node: cheerio.Cheerio<AnyNode>,
): number | null {
  const attrPrice =
    cleanText(node.find("[data-meta-price]").first().attr("data-meta-price")) ||
    ownOrDescendantText(
      node.find("[data-auto='snippet-price-current']").first(),
    ) ||
    cleanText(node.attr("data-retail-price")) ||
    cleanText(node.attr("data-price")) ||
    cleanText(
      node.find(".js-product-price[data-price-without-nds]").first().text(),
    ) ||
    cleanText(
      node
        .find(".js-product-price[data-price-without-nds]")
        .first()
        .attr("data-price-without-nds"),
    ) ||
    cleanText(node.find("[class*='price'][content]").first().attr("content")) ||
    cleanText(
      node.find("[content][itemprop='price']").first().attr("content"),
    ) ||
    cleanText(node.find("meta[itemprop='price']").first().attr("content"));
  const attrParsed = coercePrice(attrPrice);
  if (attrParsed !== null) return attrParsed;

  const priceCandidates = node.find(
    [
      ".product-buy__price",
      ".catalog-product__buy .product-buy__price",
      "[data-auto='snippet-price-current']",
      ".snippet-price__value",
      ".snippet__price .snippet-price__value",
      ".product-card__price-current",
      ".product-card__price-container .product-card__price-current",
      ".current-price",
      ".price-main",
      ".product-list-item-price",
      "[class*='price']:not([class*='old']):not([class*='credit']):not([class*='month'])",
      "[data-qa*='price']",
    ].join(", "),
  );
  let fallbackPrice: number | null = null;
  for (const element of priceCandidates.toArray()) {
    const priceText = ownOrDescendantText($(element));
    if (!priceText || /артикул|article|код товара/i.test(priceText)) continue;
    const selectedParsed = coercePrice(priceText);
    if (selectedParsed === null) continue;
    if (/₽|руб|р\.|price|цена/i.test(priceText)) return selectedParsed;
    fallbackPrice ??= selectedParsed;
  }
  if (fallbackPrice !== null) return fallbackPrice;

  const fullText = ownOrDescendantText(node);
  if (/₽|руб|р\.|цена/i.test(fullText)) {
    return coercePrice(fullText);
  }

  return null;
}

/**
 * Получает первое настоящее изображение карточки.
 *
 * @param node Корень карточки.
 * @param pageUrl Базовый URL.
 * @returns Абсолютный URL либо пустую строку для placeholder-а.
 */
function extractImageUrl(
  node: cheerio.Cheerio<AnyNode>,
  pageUrl: string,
): string {
  const image = node.find("img").first();
  const value =
    image.attr("src") ||
    image.attr("data-src") ||
    image.attr("data-original") ||
    image.attr("srcset")?.split(",", 1)[0]?.trim().split(/\s+/, 1)[0];
  if (!value || /placeholder|loader|spinner/i.test(value)) return "";
  return absoluteUrl(pageUrl, value);
}

/**
 * Определяет наличие товара по текстовым маркерам карточки.
 *
 * @param node Корень карточки.
 * @returns `true`, `false` или `undefined` при отсутствии уверенного признака.
 */
function extractStock(node: cheerio.Cheerio<AnyNode>): boolean | undefined {
  const text = ownOrDescendantText(node).toLowerCase();
  if (/нет в наличии|нет на складе|out of stock|недоступен/.test(text))
    return false;
  if (/есть в наличии|в наличии|на складе|купить|в корзину/.test(text))
    return true;
  return undefined;
}

/**
 * Извлекает товары из встроенных JSON-данных страницы.
 *
 * Ozon обрабатывается отдельным быстрым парсером. Для остальных сайтов
 * объединяются JSON-LD, состояния популярных frontend-фреймворков и
 * совместимый fallback Citilink.
 *
 * @param html Полный HTML.
 * @param pageUrl URL страницы.
 * @param profile Профиль поставщика.
 * @returns Уникальные товарные строки.
 */
function extractRowsFromEmbeddedJson(
  html: string,
  pageUrl: string,
  profile: SupplierProfile | undefined,
): ProductRow[] {
  const rows: ProductRow[] = [];

  // Ozon лучше оставить отдельным быстрым special-case:
  // его HTML часто плохо парсится обычными DOM/JSON эвристиками.
  if (profile?.domain === "ozon.ru") {
    return extractOzonRows(html, pageUrl);
  }

  // Универсальный schema.org / JSON-LD fallback.
  rows.push(...extractJsonLdProductRows(html, pageUrl));

  // Универсальный fallback для __NEXT_DATA__, __NUXT__, __INITIAL_STATE__.
  rows.push(...extractGenericStateRows(html, pageUrl));

  // Старый Citilink-specific fallback оставляем, но больше не делаем early return
  // для всех остальных сайтов.
  if (profile?.domain === "citilink.ru") {
    const productObjects =
      html.match(/"products"\s*:\s*\[[\s\S]{0,200000}?\]/g) ?? [];
    for (const productBlock of productObjects.slice(0, 3)) {
      const productMatches = productBlock.matchAll(
        /"name"\s*:\s*"([^"]{6,240})"[\s\S]{0,2500}?"price"\s*:\s*"?(\d[\d\s.,]*)"?[\s\S]{0,2500}?"url"\s*:\s*"([^"]+)"/g,
      );
      for (const match of productMatches) {
        const price = coercePrice(match[2]);
        const name = cleanText(match[1]);
        const productUrl = absoluteUrl(pageUrl, match[3].replace(/\\\//g, "/"));
        if (name && price && productUrl) {
          rows.push({ name, price, product_url: productUrl });
        }
      }
    }
  }

  return uniqueRows(rows);
}

/**
 * Извлекает карточки Ozon из повторяющихся фрагментов tile-разметки.
 *
 * @param html HTML выдачи Ozon.
 * @param pageUrl Базовый URL.
 * @returns Уникальные строки с названием, ценой и ссылкой.
 */
function extractOzonRows(html: string, pageUrl: string): ProductRow[] {
  const tileMarker =
    /<div[^>]*class="[^"]*\btile-root\b[^"]*"[^>]*data-index="\d+"[^>]*>|<div[^>]*data-index="\d+"[^>]*class="[^"]*\btile-root\b[^"]*"[^>]*>/gi;
  const productLinkMarker =
    /<a[^>]*href="\/product\/[^"]+"[^>]*class="[^"]*\btile-clickable-element\b[^"]*"/gi;
  const positions = [...html.matchAll(tileMarker)].map(
    (match) => match.index ?? 0,
  );
  if (positions.length === 0) {
    positions.push(
      ...[...html.matchAll(productLinkMarker)].map((match) => match.index ?? 0),
    );
  }

  const rows: ProductRow[] = [];
  for (let index = 0; index < positions.length; index += 1) {
    const start = positions[index] ?? 0;
    const end = positions[index + 1] ?? html.length;
    const chunk = html.slice(start, end);
    const href = chunk.match(/<a[^>]*href="(?<value>\/product\/[^"]+)"/i)
      ?.groups?.value;
    const priceText = chunk.match(
      /<span[^>]*tsHeadline500Medium[^>]*>(?<value>[^<]+)<\/span>/i,
    )?.groups?.value;
    const titleMatches = [
      ...chunk.matchAll(
        /<span[^>]*tsBody500Medium[^>]*>(?<value>[^<]+)<\/span>/gi,
      ),
    ]
      .map((match) => htmlText(match.groups?.value))
      .filter(Boolean);
    const name = titleMatches.sort(
      (left, right) => right.length - left.length,
    )[0];
    const price = coercePrice(htmlText(priceText));
    if (!href || !name || price === null) continue;
    rows.push({ name, price, product_url: absoluteUrl(pageUrl, href) });
  }
  return uniqueRows(rows);
}

/**
 * Декодирует HTML-фрагмент и возвращает его видимый текст.
 *
 * @param value Фрагмент HTML или `undefined`.
 * @returns Очищенный текст.
 */
function htmlText(value: string | undefined): string {
  if (!value) return "";
  return cleanText(cheerio.load(`<span>${value}</span>`)("span").text());
}

/**
 * Удаляет точные дубли строк по URL, имени и цене.
 *
 * @param rows Исходные строки.
 * @returns Первый экземпляр каждой уникальной комбинации.
 */
function uniqueRows(rows: ProductRow[]): ProductRow[] {
  const result: ProductRow[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const key = `${row.product_url}|${row.name}|${row.price}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(row);
  }
  return result;
}

/**
 * Преобразует внутренние строки в публичную модель результата поиска.
 *
 * @param rows Проверенные товарные строки.
 * @returns Товары с уникальными URL и положительными ценами.
 */
function buildProductsFromRows(rows: ProductRow[]): SearchProduct[] {
  const products: SearchProduct[] = [];
  const seenUrls = new Set<string>();
  for (const row of rows) {
    if (!row.name || !row.product_url || !row.price || row.price <= 0) continue;
    if (seenUrls.has(row.product_url)) continue;
    seenUrls.add(row.product_url);
    products.push({
      name: row.name,
      price: row.price,
      url: row.product_url,
      delivery_time: row.delivery_time,
      rating: row.rating,
      in_stock: row.in_stock,
    });
  }
  return products;
}

/**
 * Подсчитывает строки с отсутствующими обязательными полями.
 *
 * @param rows Анализируемые строки.
 * @returns Счётчики отсутствующих имени, цены и URL.
 */
function countRejectedRows(rows: ProductRow[]): Record<string, number> {
  const counters = {
    missing_name: 0,
    missing_price: 0,
    missing_url: 0,
  };
  for (const row of rows) {
    if (!row.name) counters.missing_name += 1;
    if (!row.price || row.price <= 0) counters.missing_price += 1;
    if (!row.product_url) counters.missing_url += 1;
  }
  return counters;
}

/**
 * Создаёт ранний результат для заблокированной или пустой страницы.
 *
 * @param args Статус, маршрут, URL, строки и диагностические маркеры.
 * @returns Результат с пустым публичным списком продуктов.
 */
function buildResult(args: {
  status: "ok" | "empty" | "blocked";
  route: string;
  pageUrl: string;
  rows: ProductRow[];
  blockedMarkers: string[];
  debug: string[];
}): ExtractProductsResult {
  return {
    status: args.status,
    route: args.route,
    pageUrl: args.pageUrl,
    rows: args.rows,
    products: [],
    diagnostics: {
      blockedMarkers: args.blockedMarkers,
      candidateCount: 0,
      rowCount: 0,
      rejectedRows: {},
      debug: args.debug,
    },
  };
}

/**
 * Расширяет найденный элемент до контейнера, объединяющего поля одного товара.
 *
 * @param $ Документ Cheerio.
 * @param node Начальный узел.
 * @param profile Профиль поставщика.
 * @returns Первый предок со ссылкой, ценой и именем либо лучший просмотренный.
 */
function expandToLikelyProductContainer(
  $: cheerio.CheerioAPI,
  node: cheerio.Cheerio<AnyNode>,
  profile: SupplierProfile | undefined,
): cheerio.Cheerio<AnyNode> {
  let current = node;
  let best = node;
  let bestScore = -1;

  for (let depth = 0; depth < 8; depth += 1) {
    const text = ownOrDescendantText(current);
    const hasProductLink = bestProductLink($, current, profile) !== null;

    const hasPrice =
      current.find(
        [
          "[data-qa='product-price-current']",
          "[data-auto='snippet-price-current']",
          "[data-qa*='price']",
          "[itemprop='price']",
          "[content][itemprop='price']",
          ".product-buy__price",
          ".catalog-product__buy .product-buy__price",
          "[data-price]",
          ".snippet-price__value",
          ".snippet__price .snippet-price__value",
          ".product-card__price-current",
          ".product-card__price-container .product-card__price-current",
          ".price-value",
          ".price_value",
          ".current-price",
          ".price-main",
          ".Product__price",
          ".v-product-price__value",
          "[class*='price']",
        ].join(", "),
      ).length > 0 || /₽|руб|р\./i.test(text);

    const hasName =
      current.find(
        [
          "a[data-qa='product-name']",
          "[data-auto='snippet-title']",
          "[data-qa='product-name']",
          ".snippet__title",
          "a.snippet__title",
          ".snippet-title",
          ".product-card__name",
          "a.product-card__name",
          ".product-card__title",
          "a.product-card__title",
          ".item-name-link",
          ".item-name a",
          ".name a",
          ".product-title",
          ".catalog-product__name",
          "[class*='product'][class*='name']",
          "[class*='product'][class*='title']",
        ].join(", "),
      ).length > 0;

    const className = cleanText(current.attr("class")).toLowerCase();
    let score = 0;
    if (hasProductLink) score += 3;
    if (hasPrice) score += 3;
    if (hasName) score += 3;
    if (
      /(^|\s)(snippet|product-card|catalog-product|product-item|catalog-item)(\s|$)/.test(
        className,
      )
    ) {
      score += 2;
    }

    if (score > bestScore && text.length < 4500) {
      best = current;
      bestScore = score;
    }

    if (hasProductLink && hasPrice && hasName) {
      return current;
    }

    const parent = current.parent();
    if (parent.length === 0 || parent.is("body") || parent.is("html")) break;
    current = parent;
  }

  return best;
}

/**
 * Извлекает товары из schema.org Product в JSON-LD.
 *
 * @param html HTML документа.
 * @param pageUrl Базовый URL.
 * @returns Уникальные строки с данными первого Offer каждого продукта.
 */
function extractJsonLdProductRows(html: string, pageUrl: string): ProductRow[] {
  const rows: ProductRow[] = [];
  const scriptMatches = html.matchAll(
    /<script[^>]+type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi,
  );

  for (const match of scriptMatches) {
    const raw = htmlText(match[1]);
    if (!raw) continue;

    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      continue;
    }

    for (const item of flattenJsonLd(parsed)) {
      if (!isRecord(item)) continue;

      const type = item["@type"];
      const types = Array.isArray(type)
        ? type.map(String)
        : [String(type ?? "")];
      if (!types.some((value) => value.toLowerCase() === "product")) continue;

      const name = cleanProductName(item.name);
      const offers = item.offers;
      const offer = Array.isArray(offers) ? offers[0] : offers;

      const price =
        isRecord(offer) && offer.price !== undefined
          ? coercePrice(offer.price)
          : null;

      const url =
        absoluteUrl(pageUrl, item.url) ||
        (isRecord(offer) ? absoluteUrl(pageUrl, offer.url) : "");

      if (name && price && url) {
        const row: ProductRow = {
          name,
          price,
          product_url: url,
        };

        const image = Array.isArray(item.image) ? item.image[0] : item.image;
        const imageUrl = absoluteUrl(pageUrl, image);
        if (imageUrl) row.image_url = imageUrl;

        rows.push(row);
      }
    }
  }

  return uniqueRows(rows);
}

/**
 * Разворачивает массивы и `@graph` JSON-LD в линейный список значений.
 *
 * @param value Разобранный JSON-LD.
 * @returns Все доступные объекты верхнего уровня и графа.
 */
function flattenJsonLd(value: unknown): unknown[] {
  if (Array.isArray(value)) return value.flatMap(flattenJsonLd);
  if (!isRecord(value)) return [value];

  const graph = value["@graph"];
  if (Array.isArray(graph)) return [value, ...graph.flatMap(flattenJsonLd)];

  return [value];
}

/**
 * Проверяет, является ли значение непустым объектом с ключами.
 *
 * @param value Неизвестное значение.
 * @returns TypeScript type guard для `Record<string, unknown>`.
 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * Извлекает товары из сериализованного состояния frontend-приложения.
 *
 * @param html HTML с `__NEXT_DATA__`, `__NUXT__` или похожими объектами.
 * @param pageUrl Базовый URL.
 * @returns До 500 уникальных товарных строк.
 */
function extractGenericStateRows(html: string, pageUrl: string): ProductRow[] {
  const rows: ProductRow[] = [];
  const jsonChunks: string[] = [];

  const nextMatch = html.match(
    /<script[^>]+id=["']__NEXT_DATA__["'][^>]*>([\s\S]*?)<\/script>/i,
  );
  if (nextMatch?.[1]) {
    jsonChunks.push(htmlText(nextMatch[1]));
  }

  const stateMatches = html.matchAll(
    /(?:window\.)?(?:__NUXT__|__INITIAL_STATE__|__APOLLO_STATE__|__REDUX_STATE__|__PRELOADED_STATE__)\s*=\s*({[\s\S]{100,800000}?})\s*;?\s*<\/script>/gi,
  );

  for (const match of stateMatches) {
    if (match[1]) {
      jsonChunks.push(match[1]);
    }
  }

  for (const chunk of jsonChunks) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(chunk);
    } catch {
      continue;
    }

    for (const object of walkObjects(parsed, 25_000)) {
      const row = rowFromUnknownObject(object, pageUrl);
      if (row) {
        rows.push(row);
      }
    }
  }

  return uniqueRows(rows).slice(0, 500);
}

/**
 * Итеративно обходит вложенные объекты без риска переполнить стек.
 *
 * @param value Корневое JSON-значение.
 * @param maxObjects Ограничение числа возвращаемых объектов.
 * @returns Найденные объекты в порядке обхода стека.
 */
function walkObjects(
  value: unknown,
  maxObjects: number,
): Record<string, unknown>[] {
  const result: Record<string, unknown>[] = [];
  const stack: unknown[] = [value];

  while (stack.length > 0 && result.length < maxObjects) {
    const current = stack.pop();

    if (Array.isArray(current)) {
      for (const item of current) {
        stack.push(item);
      }
      continue;
    }

    if (!isRecord(current)) {
      continue;
    }

    result.push(current);

    for (const child of Object.values(current)) {
      if (typeof child === "object" && child !== null) {
        stack.push(child);
      }
    }
  }

  return result;
}

/**
 * Пытается распознать товар в произвольном объекте состояния страницы.
 *
 * @param object JSON-объект.
 * @param pageUrl Базовый URL.
 * @returns Строку только при наличии правдоподобных имени, цены и товарной
 * ссылки.
 */
function rowFromUnknownObject(
  object: Record<string, unknown>,
  pageUrl: string,
): ProductRow | null {
  const name = cleanProductName(
    firstUnknownValue(object, [
      "name",
      "title",
      "productName",
      "product_name",
      "displayName",
      "display_name",
      "caption",
      "label",
    ]),
  );

  const price = coercePrice(
    firstUnknownValue(object, [
      "price",
      "currentPrice",
      "current_price",
      "actualPrice",
      "actual_price",
      "salePrice",
      "sale_price",
      "priceValue",
      "price_value",
      "cost",
      "amount",
    ]),
  );

  const rawUrl = firstUnknownValue(object, [
    "url",
    "href",
    "link",
    "productUrl",
    "product_url",
    "detailUrl",
    "detail_url",
  ]);

  const productUrl = absoluteUrl(pageUrl, rawUrl);

  if (!name || price === null || !productUrl) {
    return null;
  }

  if (name.length < 4 || name.length > 260) {
    return null;
  }

  if (!looksLikeProductHref(productUrl)) {
    return null;
  }

  const row: ProductRow = {
    name,
    price,
    product_url: productUrl,
  };

  const rawImage = firstUnknownValue(object, [
    "image",
    "imageUrl",
    "image_url",
    "picture",
    "pictureUrl",
    "picture_url",
    "photo",
    "photoUrl",
    "photo_url",
    "thumbnail",
    "thumbnailUrl",
  ]);

  const imageUrl = imageValueToUrl(rawImage, pageUrl);
  if (imageUrl) {
    row.image_url = imageUrl;
  }

  return row;
}

/**
 * Возвращает первое непустое значение среди синонимичных ключей.
 *
 * @param object Исследуемый объект.
 * @param keys Ключи в порядке приоритета.
 * @returns Найденное значение либо `undefined`.
 */
function firstUnknownValue(
  object: Record<string, unknown>,
  keys: string[],
): unknown {
  for (const key of keys) {
    const value = object[key];
    if (value !== undefined && value !== null && value !== "") {
      return value;
    }
  }
  return undefined;
}

/**
 * Преобразует строку, массив или объект изображения в абсолютный URL.
 *
 * @param value Значение поля изображения.
 * @param pageUrl Базовый URL.
 * @returns Первый доступный URL либо пустую строку.
 */
function imageValueToUrl(value: unknown, pageUrl: string): string {
  if (typeof value === "string") {
    return absoluteUrl(pageUrl, value);
  }

  if (Array.isArray(value)) {
    for (const item of value) {
      const url = imageValueToUrl(item, pageUrl);
      if (url) return url;
    }
    return "";
  }

  if (isRecord(value)) {
    return (
      absoluteUrl(pageUrl, value.url) ||
      absoluteUrl(pageUrl, value.src) ||
      absoluteUrl(pageUrl, value.href)
    );
  }

  return "";
}
