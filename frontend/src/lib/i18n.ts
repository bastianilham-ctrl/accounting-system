import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

// Auto-discovers every locales/<lang>/<namespace>.json file — adding a new
// namespace pair (id + en) is enough, no need to touch this file again.
const modules = import.meta.glob('../locales/*/*.json', { eager: true }) as Record<
  string,
  { default: Record<string, string> }
>

const resources: Record<string, Record<string, Record<string, string>>> = {}

for (const path in modules) {
  const match = path.match(/locales\/([a-z]{2})\/([a-zA-Z0-9_-]+)\.json$/)
  if (!match) continue
  const [, lang, namespace] = match
  resources[lang] ??= {}
  resources[lang][namespace] = modules[path].default
}

const STORAGE_KEY = 'language'
const savedLanguage = localStorage.getItem(STORAGE_KEY) || 'id'

i18n
  .use(initReactI18next)
  .init({
    resources,
    lng: savedLanguage,
    fallbackLng: 'id',
    ns: Object.keys(resources.id ?? {}),
    defaultNS: 'common',
    interpolation: { escapeValue: false },
  })

export function changeLanguage(lang: 'id' | 'en') {
  localStorage.setItem(STORAGE_KEY, lang)
  i18n.changeLanguage(lang)
}

export default i18n
