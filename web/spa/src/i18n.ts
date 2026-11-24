// Interface strings (Sonereli UI/UX v1). The UI follows the device language:
// Russian when navigator.language starts with "ru", English otherwise.
// Hebrew appears only in content (titles, artists, lyrics), never in chrome.
// A plain table on purpose: no i18n library, no runtime switching.

export type Lang = "en" | "ru";

export function detectLang(language: string | undefined): Lang {
  return (language ?? "").toLowerCase().startsWith("ru") ? "ru" : "en";
}

export const lang: Lang = detectLang(typeof navigator === "undefined" ? undefined : navigator.language);

const STRINGS = {
  "tab.mySongs": ["My songs", "Мои песни"],
  "tab.catalog": ["Catalog", "Каталог"],
  "tab.settings": ["Settings", "Настройки"],
  "tab.nav": ["Sections", "Разделы"],
  "action.add": ["Add song", "Добавить песню"],
  "action.sing": ["Start singing", "Начать петь"],
  "action.close": ["Close", "Закрыть"],
  "action.retry": ["Try again", "Повторить"],
  "add.title": ["Add a song", "Новая песня"],
  "add.lead": ["Paste a song link. We separate the vocals and sync the lyrics.", "Вставь ссылку на песню. Мы отделим вокал и синхронизируем текст."],
  "add.field": ["Song link", "Ссылка на песню"],
  "add.paste": ["Paste", "Вставить"],
  "add.valid": ["YouTube link · ready to prepare", "Ссылка YouTube · можно готовить"],
  "add.validOther": ["Link · ready to prepare", "Ссылка · можно готовить"],
  "add.invalid": ["In YouTube tap Share → Copy link, then paste it here.", "В YouTube нажми «Поделиться» → «Копировать ссылку» и вставь её сюда."],
  "add.or": ["or", "или"],
  "add.upload": ["Upload an audio file", "Загрузить аудиофайл"],
  "add.uploadHint": ["MP3, M4A, WAV, FLAC or OGG", "MP3, M4A, WAV, FLAC или OGG"],
  "add.remove": ["Remove file", "Убрать файл"],
  "add.linkInstead": ["Paste a link instead", "Вставить ссылку вместо файла"],
  "add.youtube": ["Straight from YouTube", "Прямо из YouTube"],
  "add.youtubeHint": ["In the YouTube app tap Share → Copy link, then come back and tap Paste.", "В приложении YouTube нажми «Поделиться» → «Копировать ссылку», вернись сюда и нажми «Вставить»."],
  "add.submit": ["Prepare song", "Подготовить песню"],
  "add.sending": ["Sending…", "Отправляем…"],
  "add.ready": ["Already in Sonereli", "Уже есть в Sonereli"],
  "add.readyLead": ["This song is ready. No waiting.", "Песня уже готова, ждать не нужно."],
  "add.offline": ["You’re offline. Your link stays here until the connection is back.", "Нет сети. Ссылка останется здесь, пока связь не вернётся."],
  "err.source": ["We couldn’t open this song. Try another link.", "Не удалось открыть песню. Попробуй другую ссылку."],
  "err.tooLarge": ["This file is too large.", "Файл слишком большой."],
  "err.fileType": ["Use an MP3, M4A, WAV, FLAC or OGG file.", "Нужен файл MP3, M4A, WAV, FLAC или OGG."],
  "err.generic": ["Something went wrong. Try again.", "Что-то пошло не так. Повтори попытку."],
  "catalog.search": ["Search the catalog", "Поиск по каталогу"],
  "catalog.placeholder": ["Song or artist · שיר או אמן", "Песня или исполнитель · שיר או אמן"],
} as const satisfies Record<string, readonly [string, string]>;

export type StringKey = keyof typeof STRINGS;

export function t(key: StringKey, language: Lang = lang): string {
  return STRINGS[key][language === "ru" ? 1 : 0];
}

export function allKeys(): StringKey[] {
  return Object.keys(STRINGS) as StringKey[];
}
