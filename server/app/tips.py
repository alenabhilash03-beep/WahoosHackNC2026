"""Authored advice for every metric and deduction in standards.yaml."""
TIPS = {
    'words_per_minute': 'Aim for 140–195 words per minute; rehearse one clear point per breath.',
    'pause_time_pct': 'Pause between points, then resume; aim for 15–30% of speaking time in pauses.',
    'time_used_pct': 'Use the minute to state your position, support it, answer an objection, and conclude.',
    'long_pause_count': 'Long pauses: you paused 2 seconds or more {value:g} times; plan your next point while finishing the current one.',
    'fillers_per_100_words': 'Replace filler words with a short silent pause while you choose the next word.',
    'vocabulary_variety_pct': 'Use precise words for each claim instead of repeating the same phrases.',
    'clarity_pct': 'Speak clearly near the microphone; recognition confidence also depends on recording quality.',
    'repeated_word_count': 'Finish the phrase before restarting it; slow down at difficult transitions.',
    'pitch_variation_pct': 'Vary your pitch across important words and contrasts; avoid forcing an unnatural voice.',
    'hedges_per_100_words': 'State your supported claim directly; remove unnecessary maybe, sort of, and I guess.',
    'uptalk_pct': 'Let completed statements end firmly; reserve rising pitch for questions.',
    'monotone_window_count': 'Mark the key word in each sentence and vary pitch when you reach it.',
    'supported_arguments': 'Back each main claim with a reason, example, or verifiable evidence.',
    'structure_points': 'State your position at the start and restate it in the conclusion.',
    'counterargument_level': 'Name the strongest opposing argument, then explain why your position still holds.',
    'connectives_per_100_words': 'Make reasoning explicit with because, therefore, and however where they fit.',
    'reasoning_fallacy_count': 'Address the claim and its evidence; replace the flagged reasoning shortcut.',
    'unsupported_claim_count': 'Add a reason or concrete example to each unsupported claim.',
    'stance_mismatch': 'Argue the side you selected; make your support for that side explicit.',
    'stance_unclear': 'Open with a clear yes or no position on the prompt.',
    'emotional_appeals': 'Connect the argument to a specific human consequence or shared value.',
    'audience_address_per_100_words': 'Connect the issue to your listeners using you, we, or our naturally.',
    'emotion_words_per_100_words': 'Describe the real human stakes with precise language; avoid exaggeration.',
    'manipulative_appeal_count': 'Replace personal attacks and fear-mongering with evidence and fair human stakes.',
}


def tip_for(metric):
    return TIPS.get(metric['metric'], 'Review the evidence for this metric and practice the flagged part.').format(value=metric.get('value', 0))
