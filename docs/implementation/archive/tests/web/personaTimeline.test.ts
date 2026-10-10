import { describe, expect, it } from "vitest";
import { PersonaTimeline } from "../lib/personaTimeline";

function setup() {
  let t = 0;
  const tl = new PersonaTimeline(() => t);
  const at = (s: number) => { t = s; };
  /** onAudio every 25 ms from a to b seconds */
  const speak = (a: number, b: number) => { for (let s = a; s <= b + 1e-9; s += 0.025) { at(Math.round(s * 1000) / 1000); tl.audio(); } };
  return { tl, at, speak };
}

describe("PersonaTimeline", () => {
  it("text before audio: opener, then a reply after the player's turn", () => {
    const { tl, at, speak } = setup();
    at(0.2); tl.agentText("Hi, I'm Dana.");          // agent_response arrives before audio
    speak(0.5, 2.0);
    at(3.0); tl.userFinal();                         // player's final transcript
    at(3.6); tl.agentText("Sure, happy to help.");
    speak(3.9, 5.2);
    expect(tl.turns).toEqual([
      { start: 0.475, end: 2, text: "Hi, I'm Dana.", interrupted: false },
      { start: 3.875, end: 5.2, text: "Sure, happy to help.", interrupted: false },
    ]);
  });

  it("text after audio starts lands in the playing turn", () => {
    const { tl, at, speak } = setup();
    speak(1.0, 1.5);
    at(1.5); tl.agentText("Late text.");
    speak(1.525, 2.0);
    expect(tl.turns).toHaveLength(1);
    expect(tl.turns[0].text).toBe("Late text.");
  });

  it("text that arrives after a silent turn finished still belongs to it while the player has not spoken", () => {
    const { tl, at, speak } = setup();
    speak(1.0, 2.0);
    at(3.0); tl.agentText("Arrived late.");
    expect(tl.turns[0].text).toBe("Arrived late.");
  });

  it("short pauses inside one answer stay one turn; a player transcript splits turns", () => {
    const { tl, at, speak } = setup();
    at(0); tl.agentText("One.");
    speak(0.1, 1.0);
    speak(1.6, 2.4);                                 // 0.6 s pause between sentences
    at(2.5); tl.agentText("Two.");                   // second sentence's text, same turn
    at(3.0); tl.userFinal();
    speak(3.3, 4.0);                                 // next turn, text comes later
    at(4.1); tl.agentText("Three.");
    const turns = tl.turns;
    expect(turns.map((x) => x.text)).toEqual(["One. Two.", "Three."]);
    expect(turns[0].end).toBe(2.4);
  });

  it("a long silence starts a new turn even without a player transcript", () => {
    const { tl, at, speak } = setup();
    at(0); tl.agentText("Hello?");
    speak(0.1, 1.0);
    at(6.0); tl.agentText("Are you still there?");  // re-prompt after silence
    speak(6.4, 7.5);
    expect(tl.turns.map((x) => x.text)).toEqual(["Hello?", "Are you still there?"]);
  });

  it("interruption and correction keep only what was spoken", () => {
    const { tl, at, speak } = setup();
    at(0); tl.agentText("I worked at Stripe for six years and then moved to a startup.", "r1");
    speak(0.2, 2.0);
    at(2.0); tl.interrupted();
    tl.correct("I worked at Stripe for six years and then moved to a startup.", "I worked at Stripe for six", "r1");
    at(3.0); tl.userFinal();
    expect(tl.turns[0]).toEqual({ start: 0.175, end: 2, text: "I worked at Stripe for six", interrupted: true });
  });

  it("same response id continues the turn even after the grace period", () => {
    const { tl, at, speak } = setup();
    at(0); tl.agentText("Part one.", "r9");
    speak(0.1, 1.0);
    at(1.9); tl.agentText("Part two.", "r9");
    expect(tl.turns[0].text).toBe("Part one. Part two.");
  });
});
