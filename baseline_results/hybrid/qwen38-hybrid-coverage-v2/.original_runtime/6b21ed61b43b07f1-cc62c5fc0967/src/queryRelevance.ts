import type {
  ProductRow,
  QueryTag,
  QueryRelevanceDiagnostics,
} from "./contracts.js";
import { cleanText } from "./utils.js";

const BM25_K1 = 1.2;
const BM25_B = 0.75;
// Порог пока сохраняется в диагностическом контракте; решение о принятии
// строки принимает строгая проверка наличия всех обязательных тегов.
const DEFAULT_THRESHOLD = 0.16;
const MAX_REJECTED_DIAGNOSTICS = 24;

const stopWords = new Set([
  "и",
  "или",
  "для",
  "под",
  "над",
  "без",
  "при",
  "по",
  "на",
  "в",
  "во",
  "из",
  "от",
  "до",
  "с",
  "со",
  "у",
  "за",
  "к",
  "ко",
  "а",
  "но",
  "же",
  "ли",
  "the",
  "and",
  "or",
  "for",
  "with",
  "безнал",
  "купить",
  "цена",
  "стоимость",
  "товар",
  "товары",
  "заказать",
]);

const measurementUnits = [
  "л",
  "литр",
  "литра",
  "литров",
  "мл",
  "мм",
  "см",
  "м",
  "кг",
  "г",
  "в",
  "v",
  "вт",
  "w",
  "а",
  "ah",
  "ач",
  "шт",
];

/**
 * Нормализует десятичный разделитель числовой части измерения.
 *
 * @param value Число в текстовом виде.
 * @returns То же значение с точкой вместо запятой.
 */
function normalizeMeasureValue(value: string): string {
  return value.replace(",", ".");
}

/**
 * Приводит распространённые единицы измерения к компактной форме.
 *
 * Например, «10 литров» становится `10л`, а «18 вольт» — `18в`. Это
 * позволяет одинаково сравнивать запрос и название товара.
 *
 * @param text Исходный запрос или текст карточки.
 * @returns Строка в нижнем регистре с нормализованными измерениями.
 */
function normalizeMeasurements(text: string): string {
  let normalized = text.toLocaleLowerCase("ru-RU");

  normalized = normalized.replace(/ё/g, "е");

  normalized = normalized.replace(
    /(\d+(?:[.,]\d+)?)\s*(литр(?:а|ов)?|л)\b/gi,
    (_match: string, value: string) => `${normalizeMeasureValue(value)}л`,
  );

  normalized = normalized.replace(
    /(\d+(?:[.,]\d+)?)\s*(миллиметр(?:а|ов)?|мм)\b/gi,
    (_match: string, value: string) => `${normalizeMeasureValue(value)}мм`,
  );

  normalized = normalized.replace(
    /(\d+(?:[.,]\d+)?)\s*(сантиметр(?:а|ов)?|см)\b/gi,
    (_match: string, value: string) => `${normalizeMeasureValue(value)}см`,
  );

  normalized = normalized.replace(
    /(\d+(?:[.,]\d+)?)\s*(килограмм(?:а|ов)?|кг)\b/gi,
    (_match: string, value: string) => `${normalizeMeasureValue(value)}кг`,
  );

  normalized = normalized.replace(
    /(\d+(?:[.,]\d+)?)\s*(ватт|ватта|ваттов|вт|w)\b/gi,
    (_match: string, value: string) => `${normalizeMeasureValue(value)}вт`,
  );

  normalized = normalized.replace(
    /(\d+(?:[.,]\d+)?)\s*(вольт|вольта|вольтов|в|v)\b/gi,
    (_match: string, value: string) => `${normalizeMeasureValue(value)}в`,
  );

  return normalized;
}

/**
 * Выполняет лёгкий русский стемминг отдельного токена.
 *
 * Числа и короткие слова сохраняются, у длинных слов удаляются наиболее
 * частые падежные и родовые окончания.
 *
 * @param token Нормализованный токен.
 * @returns Основа токена, пригодная для строгого сравнения.
 */
function stemToken(token: string): string {
  if (/^\d/.test(token)) return token;

  let normalized = token.replace(/ё/g, "е").toLocaleLowerCase("ru-RU");

  if (normalized.length <= 5) return normalized;

  normalized = normalized.replace(
    /(иями|ями|ами|ого|его|ому|ему|ыми|ими|ая|яя|ое|ее|ые|ие|ый|ий|ой|ей|ую|юю|ам|ям|ах|ях|ов|ев|ом|ем|а|я|ы|и|у|ю|е)$/u,
    "",
  );

  return normalized || token;
}

/**
 * Проверяет, является ли токен числом, объединённым с единицей измерения.
 *
 * @param token Проверяемый токен.
 * @returns `true` для значений вроде `10л` или `18в`.
 */
function isMeasurementToken(token: string): boolean {
  return new RegExp(
    `^\\d+(?:[.,]\\d+)?(?:${measurementUnits.join("|")})$`,
    "iu",
  ).test(token);
}

/**
 * Проверяет, содержит ли токен только целое или дробное число.
 *
 * @param token Проверяемый токен.
 * @returns `true`, если в токене нет букв и посторонних символов.
 */
function isNumberToken(token: string): boolean {
  return /^\d+(?:[.,]\d+)?$/.test(token);
}

/**
 * Разбивает текст товара на значимые нормализованные токены.
 *
 * Стоп-слова удаляются, единицы измерения унифицируются, а слова приводятся к
 * основам.
 *
 * @param text Анализируемый текст.
 * @returns Последовательность токенов для расчёта релевантности.
 */
function tokenize(text: string): string[] {
  const normalized = normalizeMeasurements(cleanText(text))
    .replace(/[^\p{L}\p{N}.,/-]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim();

  const rawTokens =
    normalized.match(/[\p{L}\p{N}]+(?:[.,/-][\p{L}\p{N}]+)*/gu) ?? [];

  return rawTokens
    .map((token) => token.trim().toLocaleLowerCase("ru-RU"))
    .filter(Boolean)
    .filter((token) => !stopWords.has(token))
    .filter((token) => token.length >= 2 || isNumberToken(token))
    .map(stemToken)
    .filter(Boolean);
}

/**
 * Преобразует пользовательский запрос в обязательные смысловые теги.
 *
 * @param query Поисковый запрос или пустое значение.
 * @returns Уникальные слова, числа и измерения с исходной и нормализованной
 * формой.
 */
export function splitQueryIntoTags(
  query: string | null | undefined,
): QueryTag[] {
  const raw = normalizeMeasurements(cleanText(query ?? ""));
  if (!raw) return [];

  const rawTokens = raw.match(/[\p{L}\p{N}]+(?:[.,/-][\p{L}\p{N}]+)*/gu) ?? [];
  const tags: QueryTag[] = [];
  const seen = new Set<string>();

  for (const rawToken of rawTokens) {
    const value = rawToken.trim().toLocaleLowerCase("ru-RU");
    if (!value || stopWords.has(value)) continue;

    const kind: QueryTag["kind"] = isMeasurementToken(value)
      ? "measure"
      : isNumberToken(value)
        ? "number"
        : "word";

    if (kind === "word" && value.length < 3) continue;

    const normalized = stemToken(value);
    if (!normalized || seen.has(normalized)) continue;

    seen.add(normalized);

    tags.push({
      value,
      normalized,
      kind,
      required: true,
    });
  }

  return tags;
}

/**
 * Собирает текстовые поля строки, участвующие в проверке релевантности.
 *
 * @param row Извлечённая товарная строка.
 * @returns Название, наличие и доставка, объединённые в одну строку.
 */
function rowText(row: ProductRow): string {
  return [
    row.name,
    row.availability,
    row.delivery_time,
    row.in_stock === false ? "нет в наличии" : "",
  ]
    .filter(Boolean)
    .join(" ");
}

/**
 * Подсчитывает частоту каждого токена в документе.
 *
 * @param tokens Токены товарной строки.
 * @returns Карта `токен -> число вхождений`.
 */
function termFrequency(tokens: string[]): Map<string, number> {
  const map = new Map<string, number>();

  for (const token of tokens) {
    map.set(token, (map.get(token) ?? 0) + 1);
  }

  return map;
}

/**
 * Рассчитывает BM25-оценку товарной строки относительно запроса.
 *
 * @param params Токены запроса и документа, частоты по корпусу и средняя
 * длина документа.
 * @returns Ненормализованная BM25-оценка; ноль для пустых входных данных.
 */
function bm25Score(params: {
  queryTokens: string[];
  documentTokens: string[];
  documentFrequency: Map<string, number>;
  averageDocumentLength: number;
  documentCount: number;
}): number {
  const {
    queryTokens,
    documentTokens,
    documentFrequency,
    averageDocumentLength,
    documentCount,
  } = params;

  if (!queryTokens.length || !documentTokens.length) return 0;

  const tf = termFrequency(documentTokens);
  const documentLength = documentTokens.length;
  let score = 0;

  for (const token of queryTokens) {
    const frequency = tf.get(token) ?? 0;
    if (frequency <= 0) continue;

    const df = documentFrequency.get(token) ?? 0;
    const idf = Math.log(1 + (documentCount - df + 0.5) / (df + 0.5));
    const denominator =
      frequency +
      BM25_K1 *
        (1 -
          BM25_B +
          BM25_B * (documentLength / Math.max(1, averageDocumentLength)));

    score += idf * ((frequency * (BM25_K1 + 1)) / denominator);
  }

  return score;
}

/**
 * Отбрасывает товары, в названии и сопутствующих полях которых нет тегов запроса.
 *
 * Сейчас принятие строго требует присутствия каждого тега. BM25-оценка и
 * `threshold` сохраняются в диагностике для анализа качества, но порог не
 * заменяет проверку обязательных тегов.
 *
 * @param rows Извлечённые товарные строки.
 * @param query Пользовательский запрос.
 * @param threshold Диагностический порог релевантности.
 * @returns Принятые строки, теги запроса и ограниченный список причин отказа.
 */
export function filterRowsByQueryRelevance(
  rows: ProductRow[],
  query: string | null | undefined,
  threshold = DEFAULT_THRESHOLD,
): {
  rows: ProductRow[];
  queryTags: QueryTag[];
  diagnostics: QueryRelevanceDiagnostics;
} {
  const queryTags = splitQueryIntoTags(query);
  const queryTokens = queryTags.map((tag) => tag.normalized);

  if (!rows.length || !queryTokens.length) {
    return {
      rows,
      queryTags,
      diagnostics: {
        enabled: false,
        queryTags,
        beforeCount: rows.length,
        afterCount: rows.length,
        rejectedCount: 0,
        threshold,
        rejected: [],
      },
    };
  }

  const documents = rows.map((row) => tokenize(rowText(row)));
  const averageDocumentLength =
    documents.reduce((sum, tokens) => sum + tokens.length, 0) /
    Math.max(1, documents.length);

  const documentFrequency = new Map<string, number>();

  for (const token of new Set(queryTokens)) {
    documentFrequency.set(
      token,
      documents.filter((documentTokens) => documentTokens.includes(token))
        .length,
    );
  }

  const accepted: ProductRow[] = [];
  const rejected: QueryRelevanceDiagnostics["rejected"] = [];

  rows.forEach((row, index) => {
    const documentTokens = documents[index] ?? [];
    const tokenSet = new Set(documentTokens);

    const missingTags = queryTags.filter(
      (tag) => !tokenSet.has(tag.normalized),
    );

    const score = bm25Score({
      queryTokens,
      documentTokens,
      documentFrequency,
      averageDocumentLength,
      documentCount: rows.length,
    });

    const normalizedScore = score / Math.max(1, queryTokens.length);

    if (missingTags.length === 0) {
      accepted.push(row);
      return;
    }

    if (rejected.length < MAX_REJECTED_DIAGNOSTICS) {
      rejected.push({
        name: row.name,
        score: Number(normalizedScore.toFixed(4)),
        reason: `missing_query_tags:${missingTags
          .map((tag) => tag.value)
          .join(",")}`,
      });
    }
  });

  return {
    rows: accepted,
    queryTags,
    diagnostics: {
      enabled: true,
      queryTags,
      beforeCount: rows.length,
      afterCount: accepted.length,
      rejectedCount: rows.length - accepted.length,
      threshold,
      rejected,
    },
  };
}
