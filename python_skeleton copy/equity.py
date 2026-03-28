'''
Equity estimation module.
- Preflop: Chen Formula lookup (169 distinct starting hands).
- Post-flop: Monte Carlo simulation using pkrbot.evaluate().
'''
import random
import pkrbot

RANKS = '23456789TJQKA'
ALL_CARDS = [pkrbot.Card(r + s) for r in RANKS for s in 'cdhs']
ALL_CARD_STRS = {str(c) for c in ALL_CARDS}


# ---------------------------------------------------------------------------
# Chen Formula – preflop hand strength scoring
# ---------------------------------------------------------------------------
# Score interpretation (rough thresholds for NL Hold'em):
#   >= 10 : premium (raise)
#   8-9   : strong  (raise / call raises)
#   5-7   : playable (open / call small raises)
#   < 5   : weak    (fold to aggression)
# ---------------------------------------------------------------------------

_CHEN_RANK_SCORE = {
    'A': 10, 'K': 8, 'Q': 7, 'J': 6, 'T': 5,
    '9': 4.5, '8': 4, '7': 3.5, '6': 3, '5': 2.5,
    '4': 2, '3': 1.5, '2': 1,
}


def chen_score(card1_str, card2_str):
    '''
    Compute the Chen Formula score for a 2-card starting hand.
    card1_str, card2_str: e.g. 'As', 'Kh'
    '''
    r1, s1 = card1_str[0], card1_str[1]
    r2, s2 = card2_str[0], card2_str[1]

    high = max(_CHEN_RANK_SCORE[r1], _CHEN_RANK_SCORE[r2])
    low = min(_CHEN_RANK_SCORE[r1], _CHEN_RANK_SCORE[r2])

    score = high

    # Pair bonus
    if r1 == r2:
        score = max(high * 2, 5.0)
        return score

    # Suited bonus
    if s1 == s2:
        score += 2

    # Gap penalty
    ri1 = RANKS.index(r1)
    ri2 = RANKS.index(r2)
    gap = abs(ri1 - ri2) - 1
    if gap == 1:
        score -= 1
    elif gap == 2:
        score -= 2
    elif gap == 3:
        score -= 4
    elif gap >= 4:
        score -= 5

    # Straight potential bonus for connected / one-gap low cards
    if gap <= 1 and high < 7:
        score += 1

    return score


def _hand_key(card1_str, card2_str):
    '''Canonical key for the 169-hand preflop grid: e.g. "AKs" or "72o" or "TT".'''
    r1, s1 = card1_str[0], card1_str[1]
    r2, s2 = card2_str[0], card2_str[1]
    ri1 = RANKS.index(r1)
    ri2 = RANKS.index(r2)
    high_r = r1 if ri1 >= ri2 else r2
    low_r = r2 if ri1 >= ri2 else r1
    if r1 == r2:
        return high_r + low_r
    suffix = 's' if s1 == s2 else 'o'
    return high_r + low_r + suffix


# Precompute all 169 Chen scores once at import time.
CHEN_TABLE = {}
for _r1 in RANKS:
    for _r2 in RANKS:
        for _suited in ('s', 'o'):
            if _r1 == _r2:
                key = _r1 + _r2
            else:
                ri1 = RANKS.index(_r1)
                ri2 = RANKS.index(_r2)
                if ri1 < ri2:
                    continue
                key = _r1 + _r2 + _suited
            if key in CHEN_TABLE:
                continue
            c1 = _r1 + ('c' if _suited == 's' or _r1 == _r2 else 'c')
            c2 = _r2 + ('c' if _suited == 's' else 'd')
            CHEN_TABLE[key] = chen_score(c1, c2)

# Normalise Chen scores to 0-1 equity-like range for uniform decision logic.
_CHEN_MIN = min(CHEN_TABLE.values())
_CHEN_MAX = max(CHEN_TABLE.values())


def preflop_strength(card1_str, card2_str):
    '''
    Returns a 0.0–1.0 strength estimate for a preflop hand using the Chen table.
    '''
    key = _hand_key(card1_str, card2_str)
    raw = CHEN_TABLE.get(key, 5.0)
    return (raw - _CHEN_MIN) / (_CHEN_MAX - _CHEN_MIN)


# ---------------------------------------------------------------------------
# Monte Carlo equity estimation (post-flop)
# ---------------------------------------------------------------------------

def _build_deck_and_board(my_hand_strs, board_strs, dead_cards=None):
    '''Shared setup for MC functions: build Card lists and remaining deck.'''
    my_hand = [pkrbot.Card(s) for s in my_hand_strs if s != '??']
    board = [pkrbot.Card(s) for s in board_strs if s != '??']

    known_strs = set()
    for s in my_hand_strs:
        if s != '??':
            known_strs.add(s)
    for s in board_strs:
        if s != '??':
            known_strs.add(s)
    if dead_cards:
        for s in dead_cards:
            known_strs.add(s)

    deck = [c for c in ALL_CARDS if str(c) not in known_strs]
    cards_needed = 5 - len(board)
    return my_hand, board, deck, cards_needed


def monte_carlo_equity(my_hand_strs, board_strs, num_simulations=400, dead_cards=None):
    '''
    Estimate equity (0.0–1.0) via Monte Carlo rollout.

    my_hand_strs : list of 2 card strings, e.g. ['As', 'Kh']
    board_strs   : list of 0-5 card strings (current board, may contain '??')
    dead_cards   : list of card strings known to be out of play
    num_simulations : number of random rollouts
    '''
    my_hand, board, deck, cards_needed = _build_deck_and_board(
        my_hand_strs, board_strs, dead_cards)

    wins = 0
    ties = 0

    for _ in range(num_simulations):
        random.shuffle(deck)
        opp_hand = deck[:2]
        sim_board = board + deck[2:2 + cards_needed]

        my_score = pkrbot.evaluate(my_hand + sim_board)
        opp_score = pkrbot.evaluate(opp_hand + sim_board)

        if my_score > opp_score:
            wins += 1
        elif my_score == opp_score:
            ties += 1

    return (wins + 0.5 * ties) / num_simulations


def redraw_equity(my_hand_strs, board_strs, weak_index, num_simulations=400,
                  dead_cards=None):
    '''
    Single-pass comparison: current equity vs equity after redrawing one hole card.

    Returns (current_equity, redraw_equity) both in 0.0–1.0.
    weak_index: which hole card to replace (0 or 1).
    '''
    my_hand, board, deck, cards_needed = _build_deck_and_board(
        my_hand_strs, board_strs, dead_cards)

    keep_index = 1 - weak_index

    wins_current = 0
    ties_current = 0
    wins_redraw = 0
    ties_redraw = 0

    for _ in range(num_simulations):
        random.shuffle(deck)
        opp_hand = deck[:2]
        sim_board = board + deck[2:2 + cards_needed]
        new_card = deck[2 + cards_needed]

        opp_score = pkrbot.evaluate(opp_hand + sim_board)

        my_score = pkrbot.evaluate(my_hand + sim_board)
        if my_score > opp_score:
            wins_current += 1
        elif my_score == opp_score:
            ties_current += 1

        redraw_hand = [None, None]
        redraw_hand[keep_index] = my_hand[keep_index]
        redraw_hand[weak_index] = new_card
        redraw_score = pkrbot.evaluate(redraw_hand + sim_board)
        if redraw_score > opp_score:
            wins_redraw += 1
        elif redraw_score == opp_score:
            ties_redraw += 1

    cur_eq = (wins_current + 0.5 * ties_current) / num_simulations
    rdr_eq = (wins_redraw + 0.5 * ties_redraw) / num_simulations
    return cur_eq, rdr_eq


def best_redraw_equity(my_hand_strs, board_strs, num_simulations=400,
                       dead_cards=None):
    '''
    Test redrawing EACH hole card in a single MC pass.
    Returns (current_equity, best_redraw_equity, best_redraw_index).
    '''
    my_hand, board, deck, cards_needed = _build_deck_and_board(
        my_hand_strs, board_strs, dead_cards)

    wins_cur = ties_cur = 0
    wins_r0 = ties_r0 = 0
    wins_r1 = ties_r1 = 0

    for _ in range(num_simulations):
        random.shuffle(deck)
        opp_hand = deck[:2]
        sim_board = board + deck[2:2 + cards_needed]
        new_card_a = deck[2 + cards_needed]
        new_card_b = deck[2 + cards_needed + 1]

        opp_score = pkrbot.evaluate(opp_hand + sim_board)

        my_score = pkrbot.evaluate(my_hand + sim_board)
        if my_score > opp_score:
            wins_cur += 1
        elif my_score == opp_score:
            ties_cur += 1

        score_r0 = pkrbot.evaluate([new_card_a, my_hand[1]] + sim_board)
        if score_r0 > opp_score:
            wins_r0 += 1
        elif score_r0 == opp_score:
            ties_r0 += 1

        score_r1 = pkrbot.evaluate([my_hand[0], new_card_b] + sim_board)
        if score_r1 > opp_score:
            wins_r1 += 1
        elif score_r1 == opp_score:
            ties_r1 += 1

    cur_eq = (wins_cur + 0.5 * ties_cur) / num_simulations
    eq_r0 = (wins_r0 + 0.5 * ties_r0) / num_simulations
    eq_r1 = (wins_r1 + 0.5 * ties_r1) / num_simulations

    if eq_r0 >= eq_r1:
        return cur_eq, eq_r0, 0
    return cur_eq, eq_r1, 1
