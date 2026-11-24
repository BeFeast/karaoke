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
  "action.back": ["Back", "Назад"],
  "action.cancel": ["Cancel", "Отменить"],
  "action.remove": ["Remove", "Убрать"],
  "action.more": ["More actions", "Ещё действия"],
  "proc.queued": ["Waiting to start", "Ждём очереди"],
  "proc.running": ["Preparing your song", "Готовим песню"],
  "proc.ready": ["Your song is ready", "Песня готова"],
  "proc.failed": ["We couldn’t prepare this song", "Не удалось подготовить песню"],
  "proc.cancelled": ["Preparation cancelled", "Подготовка отменена"],
  "proc.subQueued": ["Your song starts as soon as a slot frees up.", "Подготовка начнётся, как только освободится место."],
  "proc.subRunning": ["You can leave this screen. Preparation keeps going.", "Можно уйти с этого экрана, подготовка продолжится."],
  "proc.subReady": ["Vocals separated · lyrics synced.", "Вокал отделён · текст синхронизирован."],
  "proc.subSource": ["We couldn’t open this link. It may be private, removed or region-locked.", "Не удалось открыть ссылку. Видео может быть закрытым, удалённым или недоступным в регионе."],
  "proc.subFailed": ["Something went wrong while preparing. Try again.", "Во время подготовки что-то пошло не так. Повтори попытку."],
  "proc.subCancelled": ["You can prepare it again any time.", "Её можно подготовить снова в любой момент."],
  "proc.stages": ["Preparation stages", "Этапы подготовки"],
  "proc.download": ["Getting the audio", "Загружаем звук"],
  "proc.separate": ["Separating vocals", "Отделяем вокал"],
  "proc.lyrics": ["Syncing lyrics", "Синхронизируем текст"],
  "proc.final": ["Final check", "Финальная проверка"],
  "proc.done": ["Done", "Готово"],
  "proc.stopped": ["Stopped", "Остановлено"],
  "proc.hint": ["It appears in My songs when it’s ready.", "Когда песня будет готова, она появится в «Моих песнях»."],
  "proc.keepBrowsing": ["Keep browsing", "Вернуться к песням"],
  "proc.tryAnother": ["Try another link", "Другая ссылка"],
  "proc.prepareAgain": ["Prepare again", "Подготовить снова"],
  "proc.reconnecting": ["Reconnecting… showing the last update from {s} s ago.", "Переподключаемся… показано обновление {s} с назад."],
  "proc.toast": ["Your song is ready", "Песня готова"],
  "home.continue": ["Continue singing", "Продолжить"],
  "home.resume": ["Resume {title} at {time}", "Продолжить «{title}» с {time}"],
  "home.preparing": ["Preparing", "Готовятся"],
  "home.attention": ["Needs attention", "Требуют внимания"],
  "home.ready": ["Ready to sing", "Можно петь"],
  "home.couldntPrepare": ["Couldn’t prepare", "Не удалось подготовить"],
  "home.cancelled": ["Cancelled", "Отменено"],
  "home.emptyTitle": ["What’s your first song?", "С какой песни начнём?"],
  "home.emptyLead": ["Paste a YouTube link with Add song, or pick a ready song from the catalog.", "Вставь ссылку YouTube через «Добавить песню» или выбери готовую песню в каталоге."],
  "home.browse": ["Browse the catalog", "Открыть каталог"],
  "home.loading": ["Loading your songs…", "Загружаем песни…"],
  "home.error": ["Your songs didn’t load. Check the connection and try again.", "Песни не загрузились. Проверь связь и повтори попытку."],
  "home.search": ["Search my songs", "Поиск по моим песням"],
  "home.noMatch": ["No songs match.", "Ничего не нашлось."],
  "home.removeTitle": ["Remove this song?", "Убрать песню?"],
  "home.removeBody": ["“{title}” and its tracks will be removed from your songs.", "«{title}» и её дорожки будут удалены из твоих песен."],
  "home.removeAction": ["Remove {title}", "Убрать «{title}»"],
  "home.open": ["Open {title}", "Открыть «{title}»"],
  "catalog.ready": ["Ready", "Готово"],
  "catalog.prepare": ["Prepare", "Подготовить"],
  "catalog.preparing": ["Preparing · {pct}%", "Готовится · {pct}%"],
  "catalog.queued": ["Preparing", "Готовится"],
  "catalog.seeAll": ["See all", "Все"],
  "catalog.seeAllLabel": ["See all songs in {shelf}", "Все песни: {shelf}"],
  "catalog.allShelves": ["All shelves", "Все полки"],
  "catalog.count": ["{n} songs", "Песен: {n}"],
  "catalog.countOne": ["1 song", "1 песня"],
  "catalog.noResults": ["Not in the catalog yet", "В каталоге пока нет"],
  "catalog.noResultsLead": ["Add it from a YouTube link. It joins My songs when it’s ready.", "Добавь её по ссылке YouTube, и она появится в «Моих песнях», когда будет готова."],
  "catalog.empty": ["No songs here yet", "Здесь пока нет песен"],
  "catalog.error": ["The catalog didn’t load. Your own songs still work.", "Каталог не загрузился. Твои песни по-прежнему доступны."],
  "catalog.loading": ["Loading the catalog", "Загружаем каталог"],
  "catalog.clear": ["Clear search", "Очистить поиск"],
  "catalog.singLabel": ["Sing {title} by {artist}", "Петь «{title}», {artist}"],
  "catalog.prepareLabel": ["Prepare {title} by {artist}", "Подготовить «{title}», {artist}"],
  "catalog.preparingLabel": ["{title} is preparing", "«{title}» готовится"],
  "err.prepare": ["We couldn’t prepare this song. Try again or choose another version.", "Не удалось подготовить песню. Повтори попытку или выбери другую версию."],
  "catalog.search": ["Search the catalog", "Поиск по каталогу"],
  "catalog.placeholder": ["Song or artist · שיר או אמן", "Песня или исполнитель · שיר או אמן"],
} as const satisfies Record<string, readonly [string, string]>;

export type StringKey = keyof typeof STRINGS;

export function t(key: StringKey, vars?: Record<string, string | number>, language: Lang = lang): string {
  const text: string = STRINGS[key][language === "ru" ? 1 : 0];
  return vars ? text.replace(/\{(\w+)\}/g, (match, name: string) => (name in vars ? String(vars[name]) : match)) : text;
}

export function allKeys(): StringKey[] {
  return Object.keys(STRINGS) as StringKey[];
}
