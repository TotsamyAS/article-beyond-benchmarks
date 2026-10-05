import type { SupplierProfile } from "./contracts.js";
import { normalizeHostname } from "./utils.js";

// Каталог хранит устойчивые селекторы карточек и характерные фрагменты
// товарных URL для поставщиков, которым полезна предметная настройка.
const profiles: SupplierProfile[] = [
  {
    domain: "cnc.su",
    displayName: "CNC",
    itemSelector:
      "td.wrapper_td, .wrapper_td, .catalog_item, .item_block, [data-entity='item'], [class*='catalog-item']",
    productUrlHints: ["/catalog/"],
  },
  {
    domain: "etm.ru",
    displayName: "ETM",
    itemSelector:
      "[data-testid*='product'], [data-test*='product'], .product-card, [class*='product-card'], [class*='ProductCard']",
    productUrlHints: ["/cat/", "/catalog/", "/goods/"],
  },
  {
    domain: "hahn-kolb.ru",
    displayName: "Hahn Kolb",
    itemSelector:
      ".product-layout, .product-grid > div, .product-list > div, .product, .product-item, .product-tile, .product-card, [class*='product-card']",
    productUrlHints: ["/katalog/", "/product/", "/catalog/"],
  },
  {
    domain: "bigam.ru",
    displayName: "Bigam",
    itemSelector:
      ".digi-product, .product-card, .catalog-item, [class*='product-card'], [class*='productCard']",
    productUrlHints: ["/product/", "/catalog/"],
  },
  {
    domain: "chipdip.ru",
    displayName: "ChipDip",
    itemSelector: "tr.with-hover, .itemlist tr",
    productUrlHints: ["/product/"],
  },
  {
    domain: "komus.ru",
    displayName: "Komus",
    itemSelector:
      ".product-card-list, .product-card, [class*='product-card-list']",
    productUrlHints: ["/product/", "/catalog/"],
  },
  {
    domain: "dns-shop.ru",
    displayName: "DNS",
    itemSelector:
      ".catalog-product, [data-id='product'], div:has(> .catalog-product__image):has(.catalog-product__name):has(.product-buy__price)",
    fieldSelectors: {
      name: "a.catalog-product__name::attr(title), a.catalog-product__name, .catalog-product__name::attr(title), .catalog-product__name, .catalog-product__image img::attr(alt)",
      price:
        ".product-buy__price, .catalog-product__buy .product-buy__price, [class*='price']:not([class*='old']):not([class*='credit']):not([class*='month'])",
      product_url:
        "a.catalog-product__name::attr(href), a.catalog-product__image-link::attr(href), a[href*='/product/']::attr(href)",
      image_url:
        ".catalog-product__image img[data-src]::attr(data-src), .catalog-product__image img[src]::attr(src), .catalog-product__image source[srcset]::attr(srcset)",
      delivery_time:
        ".delivery-info-widget, .order-avail-wrap_main, .catalog-product__avails",
      availability: ".order-avail-wrap_main, .catalog-product__avails",
    },
    productUrlHints: ["/product/"],
  },
  {
    domain: "citilink.ru",
    displayName: "Citilink",
    itemSelector:
      "[data-meta-name='SnippetProductVerticalLayout'], [data-meta-name='ProductVerticalSnippet'], [data-meta-product-id]",
    productUrlHints: ["/product/"],
  },
  {
    domain: "mvideo.ru",
    displayName: "M.Video",
    itemSelector: "a[mvid-product-card], [mvid-product-card]",
    productUrlHints: ["/products/"],
  },
  {
    domain: "oaopolimer.ru",
    displayName: "OAO Polimer",
    itemSelector:
      ".product-item-container, li.product-item-big-card, .product-item-big-card",
    productUrlHints: ["/catalog/"],
  },
  {
    domain: "officemag.ru",
    displayName: "Офисмаг",
    itemSelector: "li.listItem.js-productListItem, .listItem",
    productUrlHints: ["/catalog/goods/"],
  },
  {
    domain: "lemanapro.ru",
    displayName: "Lemana Pro",
    itemSelector:
      "article, [data-product-id], [data-qa*='product'], [class*='product-card'], [class*='productCard']",
    productUrlHints: ["/product/", "/p/"],
  },
  {
    domain: "petrovich.ru",
    displayName: "Petrovich",
    itemSelector:
      "[data-test*='product'], [data-testid*='product'], .product-card, [class*='product-card'], [class*='ProductCard']",
    productUrlHints: ["/product/", "/catalog/"],
  },
  {
    domain: "rs24.ru",
    displayName: "RS24",
    itemSelector:
      ".search-results__item.js-product, .search-results__item, [class*='item-card'], [class*='product-card']",
    productUrlHints: ["/product/"],
  },
  {
    domain: "spb.tara.ru",
    displayName: "SPB Tara",
    itemSelector:
      ".catalog-block-view__item, .catalog_item_wrapp, .catalog_item, .item_block, [class*='product']",
    productUrlHints: ["/catalog/", "/product/"],
  },
  {
    domain: "tara.ru",
    displayName: "Tara",
    itemSelector:
      ".catalog-block-view__item, .catalog_item_wrapp, .catalog_item, .item_block, [class*='product']",
    productUrlHints: ["/catalog/", "/product/"],
  },
  {
    domain: "ximtek.ru",
    displayName: "Ximtek",
    itemSelector: ".product-item, [class*='product-item']",
    productUrlHints: ["/catalog/", "/product/"],
  },
  {
    domain: "xn----7sbbnaebi2cxajxwo0b.xn--p1ai",
    displayName: "Specodezhda Tula",
    itemSelector: ".product-list-item",
    productUrlHints: ["/catalog/"],
  },
  {
    domain: "yandex.ru",
    displayName: "Yandex Market",
    itemSelector:
      "[data-zone-name='productSnippet'], [data-baobab-name='productSnippet'], article, [class*='product-card']",
    productUrlHints: ["/product/", "/market/product", "/card/"],
  },
  {
    domain: "market.yandex.ru",
    displayName: "Yandex Market",
    itemSelector:
      "[data-zone-name='productSnippet'], [data-baobab-name='productSnippet'], article, [class*='product-card']",
    productUrlHints: ["/product/", "/market/product", "/card/"],
  },
  {
    domain: "ozon.ru",
    displayName: "Ozon",
    itemSelector:
      "[data-widget='tileV2'], [data-widget='searchResultsV2'] a[href*='/product/']",
    productUrlHints: ["/product/"],
  },
  {
    domain: "vseinstrumenti.ru",
    displayName: "Vseinstrumenti",
    itemSelector:
      "[data-qa='product-card'], a[data-qa='product-name'], a[data-qa='product-photo-click']",
    productUrlHints: ["/product/"],
  },
];

/**
 * Подбирает профиль поставщика по домену страницы или исходного источника.
 *
 * @param url Фактический URL загруженной страницы.
 * @param sourceUrl Исходный URL источника, используемый как резерв.
 * @returns Профиль точного домена или его поддомена либо `undefined`.
 */
export function getSupplierProfile(
  url: string,
  sourceUrl?: string,
): SupplierProfile | undefined {
  const host = normalizeHostname(url || sourceUrl || "");
  return profiles.find(
    (profile) => host === profile.domain || host.endsWith(`.${profile.domain}`),
  );
}

/**
 * Возвращает снимок всех встроенных профилей поставщиков.
 *
 * @returns Новый массив, который можно изменять без изменения каталога модуля.
 */
export function listSupplierProfiles(): SupplierProfile[] {
  return [...profiles];
}
