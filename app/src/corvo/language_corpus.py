"""Sentences the intent model is trained on. Runtime chat does not import this."""

from __future__ import annotations

import itertools
import random


def _join(parts: list[str]) -> str:
    return " ".join(part for part in parts if part).strip()


def training_rows() -> list[tuple[str, str]]:
    random.seed(5)
    rows: list[tuple[str, str]] = []

    def add(label: str, text: str) -> None:
        cleaned = " ".join(text.split())
        if cleaned:
            rows.append((cleaned, label))

    please = ["", "please", "kindly", "could you", "can you", "would you"]
    clear_verbs = [
        "clear", "wipe", "erase", "delete", "empty", "reset", "restart",
        "scrub", "blank", "bin", "discard", "dump", "trash", "scrap", "forget",
    ]
    clear_objects = [
        "the chat", "this chat", "the conversation", "this conversation",
        "the messages", "my messages", "the history", "the screen",
        "the transcript", "the thread", "the dialogue", "the discussion",
        "what we typed", "what I said", "everything above", "everything we have typed",
        "this whole thread", "the whole thing",
    ]
    for verb, obj, lead in itertools.product(clear_verbs, clear_objects, please):
        add("clear", _join([lead, verb, obj]))
    for extra in (
        "start over", "start again", "start fresh", "new chat", "new conversation",
        "begin afresh", "fresh start", "wipe chat", "blank the screen",
        "I want a fresh start", "let us begin again", "clean the slate",
    ):
        add("clear", extra)
        add("clear", f"please {extra}")

    cancel_bits = [
        "cancel", "never mind", "nevermind", "forget it", "drop it", "leave it",
        "stop this", "scrap the requisition", "drop this requisition",
        "I changed my mind", "don't raise it", "do not create it",
        "abort the requisition", "ignore what I was buying",
        "scrap the requisition I started", "stop the requisition I was making",
        "drop this I changed my mind", "I have changed my mind about the requisition",
    ]
    for text in cancel_bits:
        add("cancel", text)
        add("cancel", f"please {text}")

    greets = [
        "hi", "hey", "hello", "hiya", "heya", "yo", "howdy", "namaste", "hola",
        "good day", "hello there", "hey there", "hi there",
        "good to see you", "hello hope you are well", "hi friend",
    ]
    mornings = [
        "good morning", "good morning there", "morning",
        "morning hope you are doing well", "hope the morning goes well",
        "morning to you",
    ]
    afternoons = ["good afternoon", "good afternoon there", "afternoon", "hope the afternoon goes well"]
    evenings = ["good evening", "good evening there", "evening", "hope the evening goes well"]
    for text in greets:
        add("greet", text)
        add("greet", f"{text} friend")
    for text in mornings:
        add("greet_morning", text)
    for text in afternoons:
        add("greet_afternoon", text)
    for text in evenings:
        add("greet_evening", text)
    for text in (
        "thanks", "thank you", "thanks a lot", "thank you so much", "ok thanks",
        "cheers", "appreciate it", "appreciate the assistance", "many thanks",
        "thanks for the help", "thank you for that", "thanks very much",
        "ta", "much obliged", "grateful for that",
    ):
        add("thanks", text)
    for text in (
        "bye", "goodbye", "see you", "see you later", "see you soon",
        "talk later", "I'm off", "goodbye for now", "bye for now",
        "I have to go", "leaving now", "heading off", "heading out",
        "I'm heading out", "I am off", "I am leaving", "got to head off",
        "time for me to go",
    ):
        add("bye", text)
    for text in (
        "sorry", "I am sorry", "I'm sorry", "apologies", "my apologies",
        "sorry about that", "pardon me", "excuse me", "sorry I was unclear",
    ):
        add("sorry", text)
    for text in (
        "how are you", "how are you doing", "how are you doing today",
        "how's it going", "hows it going", "what's up", "whats up",
        "hope you are well", "are you there", "how have you been",
        "how's your day", "how is your day", "how's your day going",
        "how has your day been", "how's the day treating you",
        "has your day been alright", "hope your day is going well",
    ):
        add("wellbeing", text)

    help_leads = ["", "please", "can you", "could you"]
    help_bodies = [
        "help", "help me", "what can you do", "how does this work",
        "how do I use this", "show me how", "I need instructions",
        "what should I type", "explain how a requisition is raised",
        "walk me through it", "what are the steps",
        "what should I enter in this box", "where do I type",
        "what do I put in here", "how does a person raise one",
    ]
    for lead, body in itertools.product(help_leads, help_bodies):
        add("help", _join([lead, body]))

    converse_templates = [
        "I don't understand", "I do not understand", "I'm confused",
        "I am confused", "that doesn't make sense", "this doesn't work",
        "the weather is nice", "nice weather", "I'm fine", "I am good",
        "who are you", "what do you mean", "what does that mean",
        "why did you ask that", "no problem", "got it", "alright", "cool",
        "I was just talking", "just checking in", "no rush",
        "that last message was confusing", "I'm a bit lost",
        "that's all", "nothing much",
        "is everything ok",
        "I didn't follow", "can you say that differently",
        "this is only a chat", "I am not buying anything",
        "lol", "haha", "hmm", "wow",
        "the weather looks lovely", "lovely evening", "nice evening",
        "no hurry at all",
        "I was only saying hello", "that did not land",
        "a bit lost about the last reply",
        "is everything fine on your end",         "checking in, nothing to buy",
        "what you just said confused me",
        "about what you said",
        "I am lost about what you said",
        "I did not follow what you said",
    ]
    for text in converse_templates:
        add("converse", text)

    buy_frames = [
        "I need {thing}",
        "please order {thing}",
        "buy {thing}",
        "raise a pr for {thing}",
        "raise a requisition for {thing}",
        "we require {thing}",
        "can you get {thing}",
        "put through a request for {thing}",
        "I'd like {thing}",
        "we will be needing {thing}",
        "add {thing} to the requisition",
        "procure {thing}",
    ]
    things = [
        "gloves", "coated gloves", "nitrile gloves", "cotton gloves",
        "a forklift", "an electric forklift", "housekeeping",
        "housekeeping services", "bearings", "hex bolts", "hydraulic oil",
        "safety goggles", "10 gloves", "5 bearings at Bhosari",
        "gloves at Chakan tomorrow", "a crane", "welding wire",
        "a custom drinking fountain", "a portable gauge",
        "lab coats", "cutting wheels", "vci bags",
        "some bolts for the line", "monthly housekeeping at Chakan",
        "a water cooler", "an oil purifier", "a new purifier",
        "a drinking fountain", "replacement filters",
    ]
    for frame, thing in itertools.product(buy_frames, things):
        add("buy", frame.format(thing=thing))

    # Same small words appear in more than one intent, so the model cannot
    # treat "please" or "everything" as a label by themselves.
    for text, label in (
        ("please help me understand", "help"),
        ("please and thank you", "thanks"),
        ("everything is fine", "converse"),
        ("is everything alright", "converse"),
        ("can you help", "help"),
        ("can we talk", "converse"),
        ("I want to chat", "converse"),
        ("I want gloves", "buy"),
        ("start the purchase", "buy"),
        ("fresh gloves", "buy"),
        ("blank purchase order", "buy"),
    ):
        add(label, text)

    for text in (
        "yes", "no", "ok", "okay", "sure", "yep", "yup", "nope", "nah", "yeah",
        "the first one", "the second one", "the third one", "first", "second",
        "nitrile", "cotton", "tomorrow", "today", "next friday",
        "PUN", "CHK", "PUN1", "CHK1", "Bhosari", "Chakan",
        "list items", "show the items", "item list",
        "95", "100", "3", "4500", "zzz", "whenever", "asdf",
        "<script>alert(1)</script>",
    ):
        add("answer", text)

    random.shuffle(rows)
    return rows
