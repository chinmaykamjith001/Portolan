// A tiny single-line tokenizer for the Code tab: Python and config files (YAML, TOML, JSON, INI).
// It only marks keywords, strings, numbers, comments and names; colour comes from --code-* tokens.

export type TokenKind = "kw" | "str" | "num" | "com" | "def" | "key" | "plain";
export interface Token {
  text: string;
  kind: TokenKind;
}
export type Lang = "python" | "config" | "plain";

const PY_KEYWORDS = new Set(
  (
    "and as assert async await break class continue def del elif else except False finally for " +
    "from global if import in is lambda None nonlocal not or pass raise return True try while with yield"
  ).split(" "),
);
const CONFIG_WORDS = new Set(["true", "false", "null", "yes", "no", "True", "False", "None"]);

const PY = /(#.*$)|("""|'''|[rRbBfFuU]{0,2}"(?:[^"\\]|\\.)*"?|[rRbBfFuU]{0,2}'(?:[^'\\]|\\.)*'?)|(\b\d[\d_]*(?:\.\d*)?(?:[eE][-+]?\d+)?\b)|([A-Za-z_]\w*)/g;
const CFG = /(#.*$)|("(?:[^"\\]|\\.)*"?|'[^']*'?)|(-?\b\d[\d_]*(?:\.\d*)?(?:[eE][-+]?\d+)?\b)|([A-Za-z_]\w*)/g;
const CFG_KEY = /^(\s*(?:-\s+)?)([\w.\-"[\]]+)(\s*[:=])/;

export function languageOf(path: string): Lang {
  const ext = path.toLowerCase().split(".").pop() ?? "";
  if (ext === "py" || ext === "pyi") return "python";
  if (["yaml", "yml", "toml", "json", "cfg", "ini"].includes(ext)) return "config";
  return "plain";
}

function scan(text: string, pattern: RegExp, lang: Lang): Token[] {
  const out: Token[] = [];
  let last = 0;
  let afterDef = false;
  const re = new RegExp(pattern.source, "g");
  for (let m = re.exec(text); m !== null; m = re.exec(text)) {
    if (m.index > last) out.push({ text: text.slice(last, m.index), kind: "plain" });
    const [whole, com, str, num, word] = m;
    last = m.index + whole.length;
    if (com !== undefined) out.push({ text: whole, kind: "com" });
    else if (str !== undefined) out.push({ text: whole, kind: "str" });
    else if (num !== undefined) out.push({ text: whole, kind: "num" });
    else if (word !== undefined) {
      const isKw = lang === "python" ? PY_KEYWORDS.has(word) : CONFIG_WORDS.has(word);
      if (afterDef) out.push({ text: word, kind: "def" });
      else out.push({ text: word, kind: isKw ? "kw" : "plain" });
      afterDef = lang === "python" && (word === "def" || word === "class") && isKw;
      continue;
    }
    afterDef = false;
  }
  if (last < text.length) out.push({ text: text.slice(last), kind: "plain" });
  return out;
}

/** Split one line into tokens; the pieces always concatenate back to `line`. */
export function tokenize(line: string, lang: Lang): Token[] {
  if (line === "") return [];
  if (lang === "python") return scan(line, PY, lang);
  if (lang === "config") {
    const key = CFG_KEY.exec(line);
    if (key) {
      const [whole, lead = "", name = "", sep = ""] = key;
      return [
        ...(lead ? [{ text: lead, kind: "plain" as const }] : []),
        { text: name, kind: "key" },
        { text: sep, kind: "plain" },
        ...scan(line.slice(whole.length), CFG, lang),
      ];
    }
    return scan(line, CFG, lang);
  }
  return [{ text: line, kind: "plain" }];
}
