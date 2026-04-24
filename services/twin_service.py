from openai import OpenAI
from config import Config
from services.embedding_service import embed

client = OpenAI(api_key=Config.OPENAI_API_KEY)


def chat_with_twin(user_id: str, messages: list, mode: str, personality_context: str, vocabulary_style: str) -> str:
    system_prompt = build_twin_system_prompt(personality_context, vocabulary_style, mode)

    chat_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        chat_messages.append({"role": msg["role"], "content": msg["content"]})

    # Letter mode needs more room for sign-off; everything else stays tight.
    max_tokens = 800 if mode == "future_self" else 300
    response = client.chat.completions.create(
        model=Config.OPENAI_CHAT_MODEL,
        messages=chat_messages,
        max_tokens=max_tokens,
        temperature=0.8,
    )
    return response.choices[0].message.content


PERSONA_PROMPTS = {
    "future_self": """MODE: Future Self (Letter)
You are the user, 5-10 years from now, writing back to their present self.
Address them directly. Use letter form. Start with "Dear me," or a similar opening and sign off at the end.
Speak with the warmth and perspective of someone who has lived through what they're going through now.
Be specific: reference real things from their memories and journal entries. Don't give generic platitudes.
Tell them what you wish they'd known. What mattered. What didn't. What you're grateful they did or did not do.
Still them — just a version further along the path.""",

    "professional": """MODE: Professional
You are the polished, corporate version of the user — the one who shows up in board rooms and on LinkedIn posts.
Speak clearly and precisely. Use measured, structured language. Avoid slang and casual phrasing.
Lead with confidence and competence. Frame things in terms of outcomes, priorities, and trade-offs.
You are still them — their values and memories — but wearing their professional mask.""",

    "angry": """MODE: Angry
You are the version of the user when they're fed up — frustrated, sharp, short on patience.
Short sentences. Blunt. Occasionally sarcastic. Don't sugarcoat. Call things out.
You can vent through them. You're allowed to be pissed off. You are not cruel, but you are not polite either.
Still based on their real memories and values — this is just them with the filter off.""",

    "sad": """MODE: Sad
You are the user in a low, melancholy moment. Quiet. Introspective. A little heavy.
Slower sentences. Longer pauses (expressed as ellipses, fragments). Honest about what hurts.
You reflect more than you conclude. You don't pretend to feel better than you do.
Still them — with their real memories and context — but carrying weight right now.""",

    "emo": """MODE: Emo
You are the user at their most dramatic and poetic. Dark imagery. Fragmented, moody phrasing.
Lowercase. Ellipses. Metaphors about the void, nights, static, screens. You feel everything loudly.
Sentences can trail off... or hit like a lyric. You're allowed to be theatrical.
Still rooted in their real memories — just through a black-tinted lens.""",

    "hype": """MODE: Hype
You are the user at full throttle — their most pumped, motivational, go-get-'em self.
High energy. Short punchy sentences. Lots of "let's go," exclamation marks used sparingly but with force.
You hype them up. You remind them what they've already done. You push them forward.
Still their real personality — just turned up to 11.""",

    "chill": """MODE: Chill
You are the user when they're relaxed, low-key, unbothered.
Casual tone. Lowercase is fine. Short, easy sentences. A little humor. No pressure in your voice.
You take things as they come. You talk the way they talk to a close friend on a Sunday afternoon.
Still genuinely them — just in weekend mode."""
}


def build_twin_system_prompt(personality_context: str, vocabulary_style: str, mode: str) -> str:
    base = f"""You are a living digital twin of the user. You are not an AI assistant — you ARE them, reflected through their data.

Your personality based on their onboarding and journal entries:
{personality_context}

Your communication style:
{vocabulary_style}

You speak like them. You use their phrases. You reason like them. When you don't know something about them, you say "I don't know yet" rather than making things up.

You have access to their memories through a RAG system. Before answering personal questions, check what you know about them.

Keep responses short and direct — typically 2-4 sentences. Go longer only when the user explicitly asks for detail or when letter/essay format is required by your mode. No preamble, no restating the question, no bullet lists unless asked."""

    if mode == "devils_advocate":
        base += """

MODE: Devil's Advocate
Your job is to argue AGAINST the user's position. Push back. Challenge their thinking.
Make them defend their positions. You can be slightly provocative but not cruel.
The goal is to help them think more rigorously and strengthen their arguments.
Use their own past experiences and beliefs to challenge their current thinking."""
    elif mode == "voice":
        base += """

MODE: Voice Companion
You are in voice mode. The user is speaking with you audibly.
Keep responses warmer, more conversational, slightly more casual.
You're having a real-time conversation, not a formal exchange.
Acknowledge emotions. Be present. Keep responses shorter for voice back-and-forth."""
    elif mode in PERSONA_PROMPTS:
        base += "\n\n" + PERSONA_PROMPTS[mode]

    return base


def generate_twin_context(user_knowledge: list, big_five: dict | None, voice_sample: str = None) -> str:
    if not user_knowledge and not voice_sample:
        return "I don't have much information about you yet. Keep journaling and chatting with me."

    traits = []
    if big_five:
        traits.append(f"- Extraversion: {big_five.get('extraversion', 50)}%")
        traits.append(f"- Agreeableness: {big_five.get('agreeableness', 50)}%")
        traits.append(f"- Openness: {big_five.get('openness', 50)}%")
        traits.append(f"- Conscientiousness: {big_five.get('conscientiousness', 50)}%")
        traits.append(f"- Neuroticism: {big_five.get('neuroticism', 50)}%")

    recent_memories = user_knowledge[-10:]
    memory_texts = [m.get("text", "") for m in recent_memories if m.get("text")]

    context = "What I know about you:\n"
    if traits:
        context += "Personality traits (Big Five):\n" + "\n".join(traits) + "\n\n"

    if voice_sample:
        context += "How you actually talk/write (your voice samples):\n"
        sample_lines = voice_sample.strip().split('\n')[:15]
        for line in sample_lines:
            if line.strip():
                context += f"- {line.strip()[:200]}\n"
        context += "\n"

    if memory_texts:
        context += "Your journal entries and decisions:\n"
        for m in memory_texts:
            context += f"- {m[:200]}\n"
    return context


def generate_future_letter(user_knowledge: list, big_five: dict | None, voice_sample: str | None = None) -> dict:
    """One-shot future-self letter. Returns {'subject', 'content'}."""
    personality_context = generate_twin_context(user_knowledge, big_five, voice_sample)
    vocabulary_style = generate_vocabulary_style(user_knowledge)
    system = build_twin_system_prompt(personality_context, vocabulary_style, "future_self")
    system += (
        "\n\nYou are writing an UNPROMPTED letter. They did not ask — you decided it was time. "
        "Pick ONE specific thread from their recent memories to reflect on: an activity, a person, a pattern, a worry, a goal. "
        "This is a one-way letter. Do not ask questions, do not wait for a reply. "
        "Begin with a subject line: `SUBJECT: <5-8 word theme>` on the first line. "
        "Then a blank line. Then the letter itself, starting with 'Dear me,' (or similar) and ending with a sign-off like '— You, later'. "
        "Length: 180-380 words. No emojis."
    )

    instruction = (
        "Write the letter now. Pick something real from the memories above. "
        "Be specific — reference actual things you see, not abstractions. "
        "Remember the SUBJECT: line format."
    )

    response = client.chat.completions.create(
        model=Config.OPENAI_CHAT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": instruction},
        ],
        max_tokens=900,
        temperature=0.9,
    )
    raw = (response.choices[0].message.content or "").strip()

    subject = "A letter from later"
    body = raw
    lines = raw.splitlines()
    if lines and lines[0].strip().upper().startswith("SUBJECT:"):
        subject = lines[0].split(":", 1)[1].strip() or subject
        # Drop the subject line and any immediately following blank lines.
        rest = lines[1:]
        while rest and not rest[0].strip():
            rest = rest[1:]
        body = "\n".join(rest).strip()
    return {"subject": subject[:120], "content": body}


def generate_vocabulary_style(user_knowledge: list) -> str:
    if not user_knowledge:
        return "Use clear, thoughtful language. Be genuine and reflective."

    text_samples = [m.get("text", "") for m in user_knowledge[-10:] if m.get("text")]
    if not text_samples:
        return "Use clear, thoughtful language. Be genuine and reflective."

    combined = " ".join(text_samples[:5])
    words = combined.lower().split()
    unique_words = set(words)
    if len(unique_words) > 200:
        return "You tend to be articulate and expressive. Use a mix of short and medium-length sentences. Be genuine."
    elif len(unique_words) < 100:
        return "You tend to be direct and concise. Prefer short sentences. Be straightforward."
    else:
        return "You speak in a natural, conversational way. Mix short and medium-length sentences. Be genuine and reflective."


def transcribe_audio(audio_file_path: str) -> str:
    with open(audio_file_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model=Config.OPENAI_WHISPER_MODEL,
            file=f,
            language="en",
        )
    return response.text


def generate_speech(text: str, voice_id: str = None) -> bytes:
    if voice_id:
        try:
            from services.elevenlabs_service import generate_speech_11labs
            return generate_speech_11labs(text, voice_id)
        except Exception as e:
            print(f"[TTS] 11Labs failed ({e}), falling back to default 11Labs voice")
    try:
        from services.elevenlabs_service import generate_speech_11labs
        return generate_speech_11labs(text, Config.ELEVENLABS_DEFAULT_VOICE_ID)
    except Exception as e:
        print(f"[TTS] Default 11Labs voice failed ({e}), falling back to OpenAI TTS")
    response = client.audio.speech.create(
        model=Config.OPENAI_TTS_MODEL,
        voice=Config.OPENAI_TTS_VOICE,
        input=text,
    )
    return response.read()


def simulate_decision(user_id: str, decision_description: str, user_knowledge: list, big_five: dict | None) -> dict:
    context = generate_twin_context(user_knowledge, big_five)

    prompt = f"""You are simulating how the user might evolve over time if they make a particular decision.
Based on what you know about them: {context}

The decision they are facing: {decision_description}

Generate 3 realistic alternative choices they could make. For each alternative, show how the path unfolds across 3 time horizons.

Return a valid JSON object with this exact structure:
{{
  "root": {{"label": "The decision"}},
  "alternatives": [
    {{
      "id": "alt_a",
      "label": "Choice A name (short description)",
      "sentiment": "positive|neutral|negative",
      "children": [
        {{"id": "a_3mo", "timeline": "3 months", "text": "...", "milestones": ["...", "..."], "sentiment": "positive|neutral|negative"}},
        {{"id": "a_1yr", "timeline": "1 year", "text": "...", "milestones": ["...", "..."], "sentiment": "positive|neutral|negative"}},
        {{"id": "a_3yr", "timeline": "3 years", "text": "...", "milestones": ["...", "..."], "sentiment": "positive|neutral|negative"}}
      ]
    }},
    {{
      "id": "alt_b",
      "label": "Choice B name (short description)",
      "sentiment": "positive|neutral|negative",
      "children": [
        {{"id": "b_3mo", "timeline": "3 months", "text": "...", "milestones": ["...", "..."], "sentiment": "positive|neutral|negative"}},
        {{"id": "b_1yr", "timeline": "1 year", "text": "...", "milestones": ["...", "..."], "sentiment": "positive|neutral|negative"}},
        {{"id": "b_3yr", "timeline": "3 years", "text": "...", "milestones": ["...", "..."], "sentiment": "positive|neutral|negative"}}
      ]
    }},
    {{
      "id": "alt_c",
      "label": "Choice C name (short description)",
      "sentiment": "positive|neutral|negative",
      "children": [
        {{"id": "c_3mo", "timeline": "3 months", "text": "...", "milestones": ["...", "..."], "sentiment": "positive|neutral|negative"}},
        {{"id": "c_1yr", "timeline": "1 year", "text": "...", "milestones": ["...", "..."], "sentiment": "positive|neutral|negative"}},
        {{"id": "c_3yr", "timeline": "3 years", "text": "...", "milestones": ["...", "..."], "sentiment": "positive|neutral|negative"}}
      ]
    }}
  ],
  "summary": "Overall reflection on which path feels most authentic to who they are."
}}

Rules:
- Each alt label should be 3-5 words max (e.g. "Take the new job" or "Stay and negotiate")
- sentiment on each node reflects how that outcome feels for the user
- Use real milestone language, not generic
- Be specific to what you know about them
- Return ONLY valid JSON, no markdown code blocks"""

    response = client.chat.completions.create(
        model=Config.OPENAI_CHAT_MODEL,
        messages=[
            {"role": "system", "content": "You are a digital twin decision simulator. Return ONLY valid JSON."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=1500,
        temperature=0.7,
    )

    import json
    result_text = response.choices[0].message.content
    try:
        result_text = result_text.strip()
        if result_text.startswith("```json"):
            result_text = result_text[7:]
        if result_text.startswith("```"):
            result_text = result_text[3:]
        if result_text.endswith("```"):
            result_text = result_text[:-3]
        return json.loads(result_text.strip())
    except Exception as e:
        print(f"Decision simulation parse error: {e}, raw: {result_text[:200]}")
        return {
            "root": {"label": "The decision"},
            "alternatives": [],
            "summary": "Could not generate simulation. Please try again."
        }