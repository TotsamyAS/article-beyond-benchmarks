import * as cheerio from "cheerio";
import type { AnyNode } from "domhandler";

import type {
  ProductCandidate,
  ProductCandidateFieldDiagnostics,
  ProductRow,
  SupplierProfile,
} from "./contracts.js";
import {
  absoluteUrl,
  cleanProductName,
  cleanText,
  coercePrice,
  looksLikeProductHref,
  ownOrDescendantText,
  splitSelectorList,
} from "./utils.js";

const CANDIDATE_SELECTORS = [
  // explicit ecommerce / product blocks
  "[itemtype*='Product']",
  "[itemscope][itemtype*='schema.org/Product']",
  "[data-product-id]",
  "[data-product]",
  "[data-offer-id]",
  "[data-sku]",
  "[data-id='product']",
  "[data-entity='item']",
  "[data-testid*='product' i]",
  "[data-test*='product' i]",
  "[data-qa*='product' i]",

  // repeated card blocks first
  ".catalog__list-item.snippet",
  ".promo-slider__item.snippet",
  ".snippet",
  ".snippet__content",
  "[class~='snippet']",

  // class-based generic
  "[class*='product-card' i]",
  "[class*='product_card' i]",
  "[class*='productCard']",
  "[class*='product-item' i]",
  "[class*='product_item' i]",
  "[class*='catalog-item' i]",
  "[class*='catalog_item' i]",
  "[class*='goods-item' i]",
  "[class*='goods_item' i]",
  "[class*='item-card' i]",
  "[class*='card-item' i]",
  "[class*='snippet' i]",
  "[class*='tile' i]",

  // broad but useful; keep last
  "article",
  "li",
  "tr",
  ".item",
  ".card",
];

const TITLE_SELECTORS = [
  "[itemprop='name']",
  ".snippet__title",
  ".snippet__title span",
  "a.snippet__title",
  "a.snippet__title span",
  ".snippet-title",
  ".snippet-title span",
  ".product-card__name",
  "a.product-card__name",
  ".product-card__title",
  "a.product-card__title",
  "[class*='title' i]",
  "[class*='name' i]",
  "[class*='product-name' i]",
  "[class*='product-title' i]",
  "[data-qa*='name' i]",
  "[data-testid*='name' i]",
  "h1",
  "h2",
  "h3",
  "a[title]",
  "img[alt]",
];
const PRICE_SELECTORS = [
  "[itemprop='price']",
  "[content][itemprop='price']",
  "[data-price]",
  "[data-retail-price]",
  "[data-meta-price]",
  ".snippet-price__value",
  ".snippet__price .snippet-price__value",
  ".product-card__price-current",
  ".product-card__price-container .product-card__price-current",
  "[class*='price' i]:not([class*='old' i]):not([class*='credit' i]):not([class*='month' i])",
  "[data-qa*='price' i]",
  "[data-testid*='price' i]",
];

const IMAGE_SELECTORS = [
  "img[src]",
  "img[data-src]",
  "img[data-original]",
  "img[srcset]",
  "source[srcset]",
];

const BAD_TEXT_RE =
  /купить|в корзину|подробнее|сравнить|избранное|отзывы|обзор|быстрый просмотр|затрудняетесь|помогут.*определиться|материалы, которые помогут|выборе/i;
const PRICE_TEXT_RE =
  /\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+(?:[.,]\d+)?\s*(?:₽|руб\.?|р\.?)|\d+(?:[.,]\d+)?\s*(?:₽|руб\.?|р\.?)/i;

/**
 * Находит и ранжирует DOM-узлы, похожие на самостоятельные карточки товара.
 *
 * Поиск объединяет пользовательский селектор, профиль поставщика и общие
 * ecommerce-селекторы. Каждый найденный узел расширяется до наиболее
 * вероятного корня карточки, получает поля и диагностическую оценку, после
 * чего дубликаты удаляются.
 *
 * @param args Документ Cheerio, URL страницы, профиль, селектор и лимит.
 * @returns Лучшие кандидаты с полями, оценками и причинами принятия/отказа.
 */
export function discoverProductCandidates(args: {
  $: cheerio.CheerioAPI;
  pageUrl: string;
  profile?: SupplierProfile;
  itemSelector?: string | null;
  limit?: number;
}): ProductCandidate[] {
  const { $, pageUrl, profile } = args;
  const limit = Math.max(1, Math.min(100, args.limit ?? 24));

  const selectors = [
    ...splitSelectorList(args.itemSelector),
    ...splitSelectorList(profile?.itemSelector),
    ...CANDIDATE_SELECTORS,
  ];

  const seen = new Set<AnyNode>();
  const candidates: ProductCandidate[] = [];
  let inspectedNodeCount = 0;
  const maxInspectedNodes = 1500;

  for (const selector of selectors) {
    if (inspectedNodeCount >= maxInspectedNodes) break;

    let nodes: cheerio.Cheerio<AnyNode>;
    try {
      nodes = $(selector);
    } catch {
      continue;
    }

    nodes.each((_, element) => {
      if (inspectedNodeCount >= maxInspectedNodes) return false;
      inspectedNodeCount += 1;

      if (seen.has(element)) return;
      seen.add(element);

      const root = expandToCardRoot($, $(element), profile);
      const identityNode = root.get(0);
      if (!identityNode || seen.has(identityNode)) return;
      seen.add(identityNode);

      const candidate = buildCandidate({
        $,
        node: root,
        pageUrl,
        selector,
        profile,
        index: candidates.length,
      });

      if (candidate.score > 0) {
        candidates.push(candidate);
      }

      if (candidates.length >= limit * 3) return false;
      return undefined;
    });
  }

  candidates.sort((left, right) => right.score - left.score);

  return dedupeCandidates(candidates).slice(0, limit);
}

/**
 * Извлекает поля одного DOM-кандидата и рассчитывает его итоговую оценку.
 *
 * Наличие имени, цены и товарной ссылки повышает балл. Повторяемая структура
 * карточек даёт дополнительную уверенность, а слишком крупные, одиночные,
 * навигационные и рекламные контейнеры штрафуются или явно отклоняются.
 *
 * @param args Узел, документ, URL, исходный селектор, профиль и индекс.
 * @returns Полное диагностическое описание кандидата.
 */
function buildCandidate(args: {
  $: cheerio.CheerioAPI;
  node: cheerio.Cheerio<AnyNode>;
  pageUrl: string;
  selector: string;
  profile?: SupplierProfile;
  index: number;
}): ProductCandidate {
  const { $, node, pageUrl, selector, profile, index } = args;

  const fieldDiagnostics: Record<string, ProductCandidateFieldDiagnostics> = {};

  const name = extractNameField($, node);
  fieldDiagnostics.name = name.diagnostics;

  const price = extractPriceField($, node);
  fieldDiagnostics.price = price.diagnostics;

  const productUrl = extractProductUrlField($, node, pageUrl, profile);
  fieldDiagnostics.product_url = productUrl.diagnostics;

  const imageUrl = extractImageField($, node, pageUrl);
  fieldDiagnostics.image_url = imageUrl.diagnostics;

  const stock = extractStockField(node);
  fieldDiagnostics.in_stock = stock.diagnostics;

  const delivery = extractDeliveryField($, node);
  fieldDiagnostics.delivery_time = delivery.diagnostics;

  const fields: Partial<ProductRow> = {};
  if (name.value) fields.name = name.value;
  if (price.value !== null) fields.price = price.value;
  if (productUrl.value) fields.product_url = productUrl.value;
  if (imageUrl.value) fields.image_url = imageUrl.value;
  if (typeof stock.value === "boolean") fields.in_stock = stock.value;
  if (delivery.value) fields.delivery_time = delivery.value;

  const missingFields = ["name", "price", "product_url"].filter(
    (field) => !(field in fields),
  );

  const reasons: string[] = [];
  let score = 0;

  if (fields.name) {
    score += 120;
    reasons.push("has_name");
  }
  if (fields.price) {
    score += 140;
    reasons.push("has_price");
  }
  if (fields.product_url) {
    score += 130;
    reasons.push("has_product_url");
  }
  if (fields.image_url) {
    score += 30;
    reasons.push("has_image");
  }
  if (typeof fields.in_stock === "boolean") {
    score += 15;
    reasons.push("has_stock");
  }
  if (fields.delivery_time) {
    score += 10;
    reasons.push("has_delivery");
  }

  const text = cleanText(node.text());
  const html = $.html(node);
  const repetition = repeatedAncestorGroupInfo($, node);

  if (repetition.siblingCount >= 3) {
    const bonus = Math.min(220, 40 + repetition.siblingCount * 8);
    score += bonus;
    reasons.push(`repeated_card_group:${repetition.siblingCount}`);
  } else {
    score -= 70;
    reasons.push("isolated_candidate");
  }

  if (text.length < 12) {
    score -= 80;
    reasons.push("too_short");
  }
  if (text.length > 3500) {
    score -= 180;
    reasons.push("too_large");
  }
  if (node.find("form,input,select,textarea").length > 4) {
    score -= 180;
    reasons.push("too_many_controls");
  }
  if (looksLikeNavigationOrLayout(node)) {
    score -= 260;
    reasons.push("layout_or_navigation");
  }
  if (looksLikeHelpOrMarketing(node, text)) {
    score -= 360;
    reasons.push("help_or_marketing_block");
  }
  if (fields.product_url && looksLikeCategoryOnlyUrl(fields.product_url)) {
    score -= 260;
    reasons.push("category_url_not_product_url");
  }

  const explicitRejectReason = looksLikeHelpOrMarketing(node, text)
    ? "help_or_marketing_block"
    : fields.product_url && looksLikeCategoryOnlyUrl(fields.product_url)
      ? "category_url_not_product_url"
      : undefined;

  const rejectReason =
    explicitRejectReason ??
    (missingFields.length > 0
      ? `missing_${missingFields.join("_")}`
      : undefined);
  return {
    index,
    selector,
    htmlPreview: html.slice(0, 3000),
    textPreview: text.slice(0, 500),
    score,
    reasons,
    fields,
    fieldDiagnostics,
    missingFields,
    rejectReason,
  };
}

/**
 * Выбирает наиболее правдоподобное название товара внутри карточки.
 *
 * @param $ Документ Cheerio.
 * @param node Корень карточки.
 * @returns Название и объяснение выбранного селектора либо диагностику
 * отсутствия кандидата.
 */
function extractNameField(
  $: cheerio.CheerioAPI,
  node: cheerio.Cheerio<AnyNode>,
): {
  value: string | undefined;
  diagnostics: ProductCandidateFieldDiagnostics;
} {
  const raw: string[] = [];

  for (const selector of TITLE_SELECTORS) {
    const target = node.find(selector).first();
    const value =
      cleanText(target.text()) ||
      cleanText(target.attr("title")) ||
      cleanText(target.attr("aria-label")) ||
      cleanText(target.attr("alt"));

    if (value) raw.push(value);

    const normalized = cleanProductName(value);
    if (looksLikeName(normalized)) {
      return {
        value: normalized.slice(0, 240),
        diagnostics: {
          value: normalized,
          confidence: 0.9,
          reason: `selected_by_selector:${selector}`,
          raw: raw.slice(0, 8),
        },
      };
    }
  }

  const linkText = cleanText(node.find("a[href]").first().text());
  if (looksLikeName(linkText)) {
    return {
      value: cleanProductName(linkText).slice(0, 240),
      diagnostics: {
        value: linkText,
        confidence: 0.65,
        reason: "selected_from_first_link_text",
        raw: [linkText],
      },
    };
  }

  const imageAlt = cleanText(node.find("img[alt]").first().attr("alt"));
  if (looksLikeName(imageAlt)) {
    return {
      value: cleanProductName(imageAlt).slice(0, 240),
      diagnostics: {
        value: imageAlt,
        confidence: 0.6,
        reason: "selected_from_image_alt",
        raw: [imageAlt],
      },
    };
  }

  return {
    value: undefined,
    diagnostics: {
      confidence: 0,
      reason: "no_name_candidate",
      raw: raw.slice(0, 8),
    },
  };
}

/**
 * Извлекает текущую цену из специализированных узлов или общего текста.
 *
 * @param $ Документ Cheerio.
 * @param node Корень карточки.
 * @returns Числовую цену и диагностические исходные значения.
 */
function extractPriceField(
  $: cheerio.CheerioAPI,
  node: cheerio.Cheerio<AnyNode>,
): { value: number | null; diagnostics: ProductCandidateFieldDiagnostics } {
  const raw: string[] = [];

  for (const selector of PRICE_SELECTORS) {
    const target = node.find(selector).first();
    const text =
      cleanText(target.attr("content")) ||
      cleanText(target.attr("data-price")) ||
      cleanText(target.attr("data-retail-price")) ||
      cleanText(target.attr("data-meta-price")) ||
      cleanText(target.text());

    if (text) raw.push(text);

    const price = coercePrice(text);
    if (price !== null) {
      return {
        value: price,
        diagnostics: {
          value: price,
          confidence: 0.9,
          reason: `selected_by_selector:${selector}`,
          raw: raw.slice(0, 8),
        },
      };
    }
  }

  const fullText = ownOrDescendantText(node);
  const priceMatch = fullText.match(PRICE_TEXT_RE)?.[0] ?? "";
  const price = coercePrice(priceMatch);

  return {
    value: price,
    diagnostics: {
      value: price ?? undefined,
      confidence: price !== null ? 0.55 : 0,
      reason: price !== null ? "selected_from_full_text" : "no_price_candidate",
      raw: priceMatch ? [priceMatch] : raw.slice(0, 8),
    },
  };
}

/**
 * Выбирает лучшую ссылку на карточку товара.
 *
 * Ссылки оцениваются по форме URL, подсказкам профиля, классу элемента и
 * содержательности текста.
 *
 * @param $ Документ Cheerio.
 * @param node Корень карточки.
 * @param pageUrl URL документа для разрешения относительных ссылок.
 * @param profile Профиль поставщика с характерными фрагментами URL.
 * @returns Абсолютный URL и диагностику либо отсутствие значения.
 */
function extractProductUrlField(
  $: cheerio.CheerioAPI,
  node: cheerio.Cheerio<AnyNode>,
  pageUrl: string,
  profile?: SupplierProfile,
): {
  value: string | undefined;
  diagnostics: ProductCandidateFieldDiagnostics;
} {
  const hints = profile?.productUrlHints ?? [];
  let bestUrl = "";
  let bestScore = Number.NEGATIVE_INFINITY;
  const raw: string[] = [];

  const links = node.is("a[href]")
    ? node.add(node.find("a[href]"))
    : node.find("a[href]");

  links.each((_, element) => {
    const link = $(element);
    const href = cleanText(link.attr("href"));
    if (!href) return;

    raw.push(href);

    const absolute = absoluteUrl(pageUrl, href);
    const lowered = absolute.toLowerCase();

    let score = 0;
    if (looksLikeProductHref(href)) score += 10;
    if (hints.some((hint) => lowered.includes(hint))) score += 12;
    if (/title|name|product|goods|item/i.test(cleanText(link.attr("class"))))
      score += 5;
    if (cleanText(link.text()).length >= 8) score += 3;

    if (score > bestScore) {
      bestScore = score;
      bestUrl = absolute;
    }
  });

  if (bestUrl && bestScore > 0) {
    return {
      value: bestUrl,
      diagnostics: {
        value: bestUrl,
        confidence: Math.min(1, 0.35 + bestScore / 20),
        reason: "selected_best_product_link",
        raw: raw.slice(0, 8),
      },
    };
  }

  return {
    value: undefined,
    diagnostics: {
      confidence: 0,
      reason: "no_product_url_candidate",
      raw: raw.slice(0, 8),
    },
  };
}

/**
 * Извлекает первое настоящее изображение товара из карточки.
 *
 * @param $ Документ Cheerio.
 * @param node Корень карточки.
 * @param pageUrl Базовый URL.
 * @returns Абсолютный URL изображения и диагностику; placeholder-ы и
 * индикаторы загрузки пропускаются.
 */
function extractImageField(
  $: cheerio.CheerioAPI,
  node: cheerio.Cheerio<AnyNode>,
  pageUrl: string,
): {
  value: string | undefined;
  diagnostics: ProductCandidateFieldDiagnostics;
} {
  for (const selector of IMAGE_SELECTORS) {
    const image = node.find(selector).first();
    const value =
      cleanText(image.attr("src")) ||
      cleanText(image.attr("data-src")) ||
      cleanText(image.attr("data-original")) ||
      cleanText(image.attr("srcset"))?.split(",", 1)[0]?.split(/\s+/, 1)[0];

    if (value && !/placeholder|loader|spinner/i.test(value)) {
      const resolved = absoluteUrl(pageUrl, value);
      return {
        value: resolved,
        diagnostics: {
          value: resolved,
          confidence: 0.8,
          reason: `selected_by_selector:${selector}`,
          raw: [value],
        },
      };
    }
  }

  return {
    value: undefined,
    diagnostics: {
      confidence: 0,
      reason: "no_image_candidate",
      raw: [],
    },
  };
}

/**
 * Определяет наличие товара по тексту карточки.
 *
 * @param node Корень карточки.
 * @returns Логическое значение с уверенностью либо `undefined`, если текст
 * неоднозначен.
 */
function extractStockField(node: cheerio.Cheerio<AnyNode>): {
  value: boolean | undefined;
  diagnostics: ProductCandidateFieldDiagnostics;
} {
  const text = cleanText(node.text()).toLowerCase();

  if (
    /нет в наличии|нет на складе|out of stock|недоступен|нет в продаже/.test(
      text,
    )
  ) {
    return {
      value: false,
      diagnostics: {
        value: false,
        confidence: 0.75,
        reason: "negative_stock_text",
        raw: [],
      },
    };
  }

  if (
    /есть в наличии|в наличии|на складе|купить|в корзину|доступно/.test(text)
  ) {
    return {
      value: true,
      diagnostics: {
        value: true,
        confidence: 0.65,
        reason: "positive_stock_text",
        raw: [],
      },
    };
  }

  return {
    value: undefined,
    diagnostics: {
      confidence: 0,
      reason: "no_stock_candidate",
      raw: [],
    },
  };
}

/**
 * Ищет текст доставки или доступности в специализированных элементах.
 *
 * @param $ Документ Cheerio.
 * @param node Корень карточки.
 * @returns Короткий текст и диагностику либо отсутствие значения.
 */
function extractDeliveryField(
  $: cheerio.CheerioAPI,
  node: cheerio.Cheerio<AnyNode>,
): {
  value: string | undefined;
  diagnostics: ProductCandidateFieldDiagnostics;
} {
  const selectors = [
    "[class*='delivery' i]",
    "[class*='availability' i]",
    "[class*='stock' i]",
    "[data-qa*='availability' i]",
  ];

  for (const selector of selectors) {
    const text = cleanText(node.find(selector).first().text());
    if (text) {
      return {
        value: text.slice(0, 180),
        diagnostics: {
          value: text,
          confidence: 0.65,
          reason: `selected_by_selector:${selector}`,
          raw: [text],
        },
      };
    }
  }

  return {
    value: undefined,
    diagnostics: {
      confidence: 0,
      reason: "no_delivery_candidate",
      raw: [],
    },
  };
}

/**
 * Поднимается по DOM к наиболее полному, но ещё локальному корню карточки.
 *
 * Обход прекращается перед контейнером нескольких товаров или слишком большим
 * блоком. Среди просмотренных предков возвращается узел с лучшей оценкой.
 *
 * @param $ Документ Cheerio.
 * @param node Первоначально найденный элемент.
 * @param profile Профиль поставщика.
 * @returns Наиболее вероятный корень одной товарной карточки.
 */
function expandToCardRoot(
  $: cheerio.CheerioAPI,
  node: cheerio.Cheerio<AnyNode>,
  profile?: SupplierProfile,
): cheerio.Cheerio<AnyNode> {
  let current = node;
  let best = node;
  let bestScore = scoreContainer($, node, profile);

  for (let depth = 0; depth < 7; depth += 1) {
    const parent = current.parent();
    if (!parent.length || parent.is("body") || parent.is("html")) break;

    const score = scoreContainer($, parent, profile) - depth * 5;
    const textLength = cleanText(parent.text()).length;
    const productChildCount = repeatedProductChildCount($, parent);

    if (productChildCount >= 2 || textLength > 4500) break;

    if (score > bestScore) {
      best = parent;
      bestScore = score;
    }

    current = parent;
  }

  return best;
}

/**
 * Оценивает пригодность DOM-контейнера как одной товарной карточки.
 *
 * @param $ Документ Cheerio.
 * @param node Проверяемый контейнер.
 * @param profile Профиль с подсказками товарных URL.
 * @returns Балл на основе атрибутов, цены, ссылок, изображения и размера.
 */
function scoreContainer(
  $: cheerio.CheerioAPI,
  node: cheerio.Cheerio<AnyNode>,
  profile?: SupplierProfile,
): number {
  const text = cleanText(node.text());
  const attrs = [
    node.attr("id"),
    node.attr("class"),
    node.attr("data-product-id"),
    node.attr("data-offer-id"),
    node.attr("data-sku"),
    node.attr("data-qa"),
    node.attr("data-testid"),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  let score = 0;

  if (/product|goods|catalog|item|card|snippet|tile|offer|sku/.test(attrs))
    score += 80;
  if (PRICE_TEXT_RE.test(text)) score += 80;
  if (node.find("a[href]").length > 0) score += 40;
  if (node.find("img,source").length > 0) score += 25;
  if (profile?.productUrlHints?.some((hint) => node.html()?.includes(hint)))
    score += 60;

  if (text.length < 15) score -= 80;
  if (text.length > 3500) score -= 140;
  if (looksLikeNavigationOrLayout(node)) score -= 180;

  return score;
}

/**
 * Считает прямых потомков, каждый из которых сам похож на товар.
 *
 * @param $ Документ Cheerio.
 * @param node Родительский контейнер.
 * @returns Число вероятных карточек среди непосредственных детей.
 */
function repeatedProductChildCount(
  $: cheerio.CheerioAPI,
  node: cheerio.Cheerio<AnyNode>,
): number {
  let count = 0;
  node.children().each((_, element) => {
    const child = $(element);
    if (scoreContainer($, child) >= 100) count += 1;
  });
  return count;
}

/**
 * Проверяет, является ли узел навигацией или общим элементом макета.
 *
 * @param node DOM-контейнер.
 * @returns `true` для header/footer, меню, фильтров, боковых панелей и
 * похожих служебных блоков.
 */
function looksLikeNavigationOrLayout(node: cheerio.Cheerio<AnyNode>): boolean {
  const attrs = [
    node.prop("tagName"),
    node.attr("id"),
    node.attr("class"),
    node.attr("role"),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  const text = cleanText(node.text()).toLowerCase();

  return (
    /header|footer|nav|menu|filter|pagination|breadcrumb|modal|popup|layout|wrapper|sidebar|aside|catalog__aside|help-choosing/.test(
      attrs,
    ) ||
    /личный кабинет|корзина|избранное|сравнение|сортировать|фильтр|затрудняетесь в выборе|помогут вам определиться/.test(
      text,
    )
  );
}

/**
 * Измеряет повторяемость структуры узла среди соседей на нескольких уровнях.
 *
 * @param $ Документ Cheerio.
 * @param node Проверяемый кандидат.
 * @returns Максимальное число соседей с одинаковой структурной подписью и
 * саму подпись.
 */
function repeatedAncestorGroupInfo(
  $: cheerio.CheerioAPI,
  node: cheerio.Cheerio<AnyNode>,
): { siblingCount: number; signature: string } {
  let current = node;
  let best = {
    siblingCount: 1,
    signature: structuralSignature(node),
  };

  for (let depth = 0; depth < 6; depth += 1) {
    const signature = structuralSignature(current);
    if (signature) {
      let count = 0;
      const parent = current.parent();

      parent.children().each((_, element) => {
        if (structuralSignature($(element)) === signature) {
          count += 1;
        }
      });

      if (count > best.siblingCount) {
        best = { siblingCount: count, signature };
      }
    }

    const parent = current.parent();
    if (!parent.length || parent.is("body") || parent.is("html")) break;
    current = parent;
  }

  return best;
}

/**
 * Строит грубую подпись структуры товарного узла.
 *
 * @param node DOM-узел.
 * @returns Комбинацию тега, значимых классов и data-атрибутов либо пустую
 * строку для неинформативного узла.
 */
function structuralSignature(node: cheerio.Cheerio<AnyNode>): string {
  const tag = cleanText(node.prop("tagName")).toLowerCase();
  const classTokens = cleanText(node.attr("class"))
    .toLowerCase()
    .split(/\s+/)
    .filter((token) =>
      /product|goods|catalog|item|card|snippet|tile|offer/.test(token),
    )
    .sort()
    .slice(0, 8)
    .join(".");

  const dataAttrs = [
    "data-product-id",
    "data-offer-id",
    "data-sku",
    "data-id",
    "data-code",
  ]
    .filter((attr) => cleanText(node.attr(attr)))
    .join("|");

  if (!tag && !classTokens && !dataAttrs) return "";
  return `${tag}|${classTokens}|${dataAttrs}`;
}

/**
 * Распознаёт консультационные, рекламные и вспомогательные блоки.
 *
 * @param node DOM-контейнер.
 * @param text Уже извлечённый текст контейнера.
 * @returns `true`, если атрибуты или формулировки указывают не на товар.
 */
function looksLikeHelpOrMarketing(
  node: cheerio.Cheerio<AnyNode>,
  text: string,
): boolean {
  const attrs = [
    node.prop("tagName"),
    node.attr("id"),
    node.attr("class"),
    node.attr("role"),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  const normalizedText = cleanText(text).toLowerCase();

  return (
    /help-choosing|help|consult|banner|promo|sidebar|aside/.test(attrs) ||
    /затрудняетесь в выборе|помогут вам определиться|материалы, которые помогут/.test(
      normalizedText,
    )
  );
}

/**
 * Проверяет, является ли URL только числовой категорией каталога.
 *
 * @param value Абсолютная или относительная ссылка.
 * @returns `true` для маршрута вида `/catalog/1852/`, который недостаточно
 * конкретен для карточки товара.
 */
function looksLikeCategoryOnlyUrl(value: string): boolean {
  let pathname = "";
  try {
    pathname = new URL(value).pathname.toLowerCase();
  } catch {
    pathname = value.toLowerCase();
  }

  return /^\/catalog\/\d+\/?$/.test(pathname);
}

/**
 * Проверяет строку на пригодность в качестве названия товара.
 *
 * @param value Текстовый кандидат.
 * @returns `false` для слишком коротких, длинных, служебных и чисто ценовых
 * строк.
 */
function looksLikeName(value: string): boolean {
  const text = cleanText(value);
  if (text.length < 4 || text.length > 260) return false;
  if (BAD_TEXT_RE.test(text)) return false;
  if (/^\d[\d\s.,]*(?:₽|руб\.?|р\.?)?$/i.test(text)) return false;
  return /[a-zа-яё]/i.test(text);
}

/**
 * Удаляет повторные кандидаты по URL или сочетанию имени и цены.
 *
 * @param candidates Кандидаты в порядке предпочтения.
 * @returns Первый экземпляр каждого уникального товара.
 */
function dedupeCandidates(candidates: ProductCandidate[]): ProductCandidate[] {
  const seen = new Set<string>();
  const result: ProductCandidate[] = [];

  for (const candidate of candidates) {
    const key =
      candidate.fields.product_url ||
      `${candidate.fields.name ?? ""}|${candidate.fields.price ?? ""}`;

    if (!key || seen.has(String(key))) continue;

    seen.add(String(key));
    result.push(candidate);
  }

  return result;
}
