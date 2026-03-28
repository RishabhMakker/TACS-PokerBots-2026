'''
Equity-based pokerbot.
'''
from skeleton.actions import FoldAction, CallAction, CheckAction, RaiseAction, RedrawAction
from skeleton.states import NUM_ROUNDS, STARTING_STACK, BIG_BLIND
from skeleton.bot import Bot
from skeleton.runner import parse_args, run_bot
from equity import preflop_strength, monte_carlo_equity

RANKS = '23456789TJQKA'

# Adaptive sim count: more sims when fewer unknowns (more accurate),
# fewer sims preflop (handled by Chen table anyway).
SIMS_BY_STREET = {0: 0, 3: 400, 4: 300, 5: 200}

# Time threshold (seconds) below which we cut sim count to survive.
LOW_TIME_THRESHOLD = 15.0
LOW_TIME_SIMS = 100


class Player(Bot):
    '''
    A pokerbot.
    '''

    def __init__(self):
        self.last_round_num = 0
        self.bankroll = 0
        self.game_clock = 0.0
        self.round_num = 0
        self.is_button = False
        self.my_cards = []
        self.opp_cards = []
        self.my_delta = 0
        self.dead_cards = []
        self._cached_street = -1
        self._cached_equity = 0.5

    def handle_new_round(self, game_state, round_state, active):
        self.last_round_num = game_state.round_num
        self.round_num = game_state.round_num
        self.bankroll = game_state.bankroll
        self.game_clock = game_state.game_clock
        self.is_button = (active == 0)
        self.my_cards = list(round_state.hands[active])
        self.dead_cards = []
        self._cached_street = -1
        self._cached_equity = 0.5

    def handle_round_over(self, game_state, terminal_state, active):
        self.bankroll = game_state.bankroll
        self.game_clock = game_state.game_clock
        self.my_delta = terminal_state.deltas[active]
        self.opp_cards = list(terminal_state.previous_state.hands[1 - active])

    # ------------------------------------------------------------------
    # Equity helpers
    # ------------------------------------------------------------------

    def _get_equity(self, my_cards, board, street, game_clock, force_mc=False):
        '''Return equity, using cache if the street hasn't changed.'''
        if street == self._cached_street and not force_mc:
            return self._cached_equity

        if street == 0 and not force_mc:
            valid = [c for c in my_cards if c and c != '??']
            if len(valid) == 2:
                equity = preflop_strength(valid[0], valid[1])
            else:
                equity = 0.5
        else:
            sims = SIMS_BY_STREET.get(street, 300)
            if force_mc and street == 0:
                sims = 200
            if game_clock < LOW_TIME_THRESHOLD:
                sims = LOW_TIME_SIMS
            equity = monte_carlo_equity(my_cards, board, num_simulations=sims,
                                        dead_cards=self.dead_cards or None)

        self._cached_street = street
        self._cached_equity = equity
        return equity

    # ------------------------------------------------------------------
    # Redraw logic
    # ------------------------------------------------------------------

    def _rank_value(self, card):
        if not card or card == '??':
            return -1
        try:
            return RANKS.index(card[0])
        except ValueError:
            return -1

    def _weakest_hole_index(self, my_cards):
        v0 = self._rank_value(my_cards[0])
        v1 = self._rank_value(my_cards[1])
        return 0 if v0 <= v1 else 1

    def _should_redraw(self, round_state, active, equity):
        if round_state.redraws_used[active]:
            return False
        if round_state.street not in (3, 4):
            return False
        return equity < 0.35

    # ------------------------------------------------------------------
    # Main decision
    # ------------------------------------------------------------------

    def _safe_action(self, legal_actions):
        '''Fallback action that never crashes.'''
        if CheckAction in legal_actions:
            return CheckAction()
        return FoldAction()

    def get_action(self, game_state, round_state, active):
        try:
            return self._decide(game_state, round_state, active)
        except Exception:
            return self._safe_action(round_state.legal_actions())

    def _decide(self, game_state, round_state, active):
        legal_actions = round_state.legal_actions()
        my_cards = round_state.hands[active]
        board = round_state.board
        street = round_state.street
        my_pip = round_state.pips[active]
        opp_pip = round_state.pips[1 - active]
        my_stack = round_state.stacks[active]
        opp_stack = round_state.stacks[1 - active]
        continue_cost = opp_pip - my_pip
        pot = (STARTING_STACK - my_stack) + (STARTING_STACK - opp_stack)
        pot_odds = continue_cost / (pot + continue_cost) if continue_cost > 0 else 0
        min_raise, max_raise = round_state.raise_bounds() if RaiseAction in legal_actions else (0, 0)

        is_all_in = continue_cost >= my_stack
        force_mc = is_all_in and street == 0
        equity = self._get_equity(my_cards, board, street, game_state.game_clock,
                                  force_mc=force_mc)

        # ---- Redraw check (flop / turn only, when equity is low) ----
        if RedrawAction in legal_actions and self._should_redraw(round_state, active, equity):
            target_index = self._weakest_hole_index(my_cards)
            inner = self._betting_action(equity, pot_odds, continue_cost,
                                         my_stack, min_raise, max_raise, legal_actions)
            return RedrawAction('hole', target_index, inner)

        return self._betting_action(equity, pot_odds, continue_cost,
                                    my_stack, min_raise, max_raise, legal_actions)

    def _betting_action(self, equity, pot_odds, continue_cost,
                        my_stack, min_raise, max_raise, legal_actions):
        '''Pure equity-vs-pot-odds decision.'''

        is_all_in = continue_cost >= my_stack

        # --- All-in defense: require strong equity to call a shove ---
        if is_all_in:
            if equity >= 0.55:
                return CallAction() if CallAction in legal_actions else CheckAction()
            if CheckAction in legal_actions:
                return CheckAction()
            return FoldAction()

        # --- Very strong hand: raise aggressively ---
        if equity >= 0.75 and RaiseAction in legal_actions:
            raise_amount = int(min_raise + 0.7 * (max_raise - min_raise))
            return RaiseAction(max(min_raise, min(raise_amount, max_raise)))

        # --- Strong hand: raise moderately ---
        if equity >= 0.6 and RaiseAction in legal_actions:
            raise_amount = int(min_raise + 0.35 * (max_raise - min_raise))
            return RaiseAction(max(min_raise, min(raise_amount, max_raise)))

        # --- Decent hand: comfortably above pot odds, call ---
        if equity >= pot_odds + 0.1:
            if continue_cost == 0 and equity >= 0.5 and RaiseAction in legal_actions:
                return RaiseAction(min_raise)
            if CheckAction in legal_actions:
                return CheckAction()
            return CallAction()

        # --- Marginal: barely above pot odds, call ---
        if equity >= pot_odds:
            if CheckAction in legal_actions:
                return CheckAction()
            return CallAction()

        # --- Below pot odds: fold (or check if free) ---
        if CheckAction in legal_actions:
            return CheckAction()
        return FoldAction()


if __name__ == '__main__':
    run_bot(Player(), parse_args())
