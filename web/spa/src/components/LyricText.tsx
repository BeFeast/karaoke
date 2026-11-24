import { type CSSProperties, Fragment, memo, useMemo } from "react";
import type { TimedLine, TimedWord } from "./stage-core";

interface LyricToken {
  text: string;
  space: string;
  offset: number;
  word?: TimedWord;
  wordOffset: number;
}

function phrasesFor(line: TimedLine): LyricToken[][] {
  // Keep the exact displayed text, including punctuation between timing tags.
  // Walk offsets in order so recurring words retain their own timestamps.
  let cursor = 0;
  const words = (line.words ?? []).flatMap((word) => {
    const offset = line.text.indexOf(word.text, cursor);
    if (offset < 0) return [];
    cursor = offset + word.text.length;
    return [{ offset, end: cursor, word }];
  });
  let wordIndex = 0;
  const phrases: LyricToken[][] = [];
  let phrase: LyricToken[] = [];
  for (const match of line.text.matchAll(/(\S+)(\s*)/gu)) {
    const [, text, space] = match;
    const offset = match.index;
    while (wordIndex < words.length && words[wordIndex].end <= offset) wordIndex++;
    const timing = words[wordIndex];
    const timed = timing && timing.offset < offset + text.length;
    phrase.push({ text, space, offset, word: timed ? timing.word : undefined,
      wordOffset: timed ? offset - timing.offset : 0 });
    if (space && /[,.!?;:…׃؟،][”’"'»)}\]]*$/u.test(text)) {
      phrases.push(phrase);
      phrase = [];
    }
  }
  if (phrase.length) phrases.push(phrase);
  return phrases;
}

function tokenProgress(token: LyricToken, line: TimedLine, time: number): number {
  if (line.words && !token.word) return 0;
  const { t, d, text } = token.word ?? line;
  const fraction = time < t ? 0 : d > 0 ? Math.min(1, (time - t) / d) : 1;
  const offset = token.word ? token.wordOffset : token.offset;
  return Math.max(0, Math.min(1, (fraction * text.length - offset) / token.text.length)) * 100;
}

export const LyricText = memo(function LyricText({
  line,
  currentTime,
  reducedMotion = false,
  neighbor = false,
}: {
  line: TimedLine;
  currentTime?: number;
  reducedMotion?: boolean;
  neighbor?: boolean;
}) {
  const phrases = useMemo(() => phrasesFor(line), [line]);
  const active = currentTime !== undefined;
  // The word being sung now (cyan underline on the Stage): the last word that
  // has started. Lines without word timing get no underline.
  let nowWord: TimedWord | undefined;
  if (active && line.words) {
    for (const word of line.words) if (word.t <= currentTime) nowWord = word;
  }
  return (
    <span className="lyric-text" dir="auto" data-lyric-active={active || undefined}
      data-lyric-neighbor={neighbor || undefined}>
      {phrases.map((phrase) => (
        <Fragment key={phrase[0].offset}>
          <span className="lyric-phrase">
            {phrase.map((token, index) => (
              <Fragment key={token.offset}>
                <span
                  className={active ? "lyric-token lyric-token-timed" : "lyric-token"}
                  data-lyric-word-start={token.word && token.wordOffset <= 0 ? token.word.t : undefined}
                  data-now={nowWord && token.word === nowWord ? "" : undefined}
                  style={active ? {
                    "--lyric-progress": `${reducedMotion ? 100 : tokenProgress(token, line, currentTime)}%`,
                  } as CSSProperties : undefined}
                >{token.text}</span>
                {index < phrase.length - 1 ? token.space : null}
              </Fragment>
            ))}
          </span>
          {phrase[phrase.length - 1].space}
        </Fragment>
      ))}
    </span>
  );
});
