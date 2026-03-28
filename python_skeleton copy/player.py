'''
Exploitative equity bot: better equity estimation, range-adjusted decisions,
tighter pot-odds discipline, improved redraws, and consistent bet sizing.
'''
from skeleton.actions import FoldAction, CallAction, CheckAction, RaiseAction, RedrawAction
from skeleton.states import NUM_ROUNDS, STARTING_STACK, BIG_BLIND
from skeleton.bot import Bot
from skeleton.runner import parse_args, run_bot
from equity import monte_carlo_equity, best_redraw_equity

RANKS = '23456789TJQKA'

# Use MC for ALL streets including preflop.
SIMS_BY_STREET = {0: 200, 3: 500, 4: 400, 5: 300}

LOW_TIME_THRESHOLD = 15.0
LOW_TIME_SIMS = 80

# Redraw threshold: take any improvement >= 2% (redraws are free).
REDRAW_THRESHOLD = 0.02

# Pot-odds safety margin: only 5% buffer instead of the typical 10%.
POT_ODDS_MARGIN = 0.05

# Consistent raise sizing: always bet ~60% pot to avoid leaking hand strength.
RAISE_POT_FRACTION = 0.60

# Equity discount: scale down our equity when facing opponent aggression.
# A bet of X% of pot → multiply equity by (1 - DISCOUNT_RATE * X).
DISCOUNT_RATE = 0.22
DISCOUNT_FLOOR = 0.60


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
        self._prev_board = []
        self._prev_opp_redraws_used = False

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
        self._prev_board = list(round_state.board)
        self._prev_opp_redraws_used = False

    def handle_round_over(self, game_state, terminal_state, active):
        self.bankroll = game_state.bankroll
        self.game_clock = game_state.game_clock
        self.my_delta = terminal_state.deltas[active]
        self.opp_cards = list(terminal_state.previous_state.hands[1 - active])

    # ------------------------------------------------------------------
    # Equity helpers
    # ------------------------------------------------------------------

    def _get_equity(self, my_cards, board, street, game_clock):
        '''Return MC equity for any street, using cache when possible.'''
        if street == self._cached_street:
            return self._cached_equity

        sims = SIMS_BY_STREET.get(street, 300)
        if game_clock < LOW_TIME_THRESHOLD:
            sims = LOW_TIME_SIMS
        equity = monte_carlo_equity(my_cards, board, num_simulations=sims,
                                    dead_cards=self.dead_cards or None)

        self._cached_street = street
        self._cached_equity = equity
        return equity

    def _discount_equity(self, equity, continue_cost, pot):
        '''Reduce our equity estimate when the opponent bets.
        Bigger bets → opponent likely has a stronger hand → our equity is lower
        than the raw MC estimate against a random hand.'''
        if continue_cost <= 0 or pot <= 0:
            return equity
        bet_fraction = min(continue_cost / pot, 1.5)
        multiplier = max(DISCOUNT_FLOOR, 1.0 - DISCOUNT_RATE * bet_fraction)
        return equity * multiplier

    # ------------------------------------------------------------------
    # Dead card tracking
    # ------------------------------------------------------------------

    def _detect_opponent_board_redraw(self, round_state, active):
        '''Check if opponent redrew a board card; if so, add the old card to dead_cards.'''
        opp = 1 - active
        opp_used_now = round_state.redraws_used[opp]
        if opp_used_now and not self._prev_opp_redraws_used:
            for i, old_card in enumerate(self._prev_board):
                if old_card and old_card != '??' and i < len(round_state.board):
                    new_card = round_state.board[i]
                    if new_card == '??' or new_card != old_card:
                        if old_card not in self.dead_cards:
                            self.dead_cards.append(old_card)
        self._prev_opp_redraws_used = opp_used_now
        self._prev_board = list(round_state.board)

    # ------------------------------------------------------------------
    # Redraw logic (tests BOTH hole cards via MC)
    # ------------------------------------------------------------------

    def _should_redraw(self, round_state, active, my_cards, board, game_clock):
        '''MC-test redrawing each hole card; pick whichever helps more.
        Returns (should_redraw, current_equity, best_index).'''
        if round_state.redraws_used[active]:
            return False, 0.5, 0
        if round_state.street not in (3, 4):
            return False, 0.5, 0

        sims = SIMS_BY_STREET.get(round_state.street, 300)
        if game_clock < LOW_TIME_THRESHOLD:
            sims = LOW_TIME_SIMS

        cur_eq, best_rdr_eq, best_idx = best_redraw_equity(
            my_cards, board, num_simulations=sims,
            dead_cards=self.dead_cards or None)

        improvement = best_rdr_eq - cur_eq
        return improvement >= REDRAW_THRESHOLD, cur_eq, best_idx

    # ------------------------------------------------------------------
    # Main decision
    # ------------------------------------------------------------------

    def _safe_action(self, legal_actions):
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
        min_raise, max_raise = round_state.raise_bounds() if RaiseAction in legal_actions else (0, 0)

        self._detect_opponent_board_redraw(round_state, active)

        # --- Redraw check: MC both cards, pick the best swap ---
        if RedrawAction in legal_actions and street in (3, 4):
            should_redraw, equity, best_idx = self._should_redraw(
                round_state, active, my_cards, board, game_state.game_clock)
            self._cached_street = street
            self._cached_equity = equity
            if should_redraw:
                inner = self._betting_action(equity, continue_cost,
                                             my_stack, min_raise, max_raise,
                                             pot, legal_actions)
                return RedrawAction('hole', best_idx, inner)
        else:
            equity = self._get_equity(my_cards, board, street, game_state.game_clock)

        return self._betting_action(equity, continue_cost,
                                    my_stack, min_raise, max_raise,
                                    pot, legal_actions)

    def _betting_action(self, raw_equity, continue_cost,
                        my_stack, min_raise, max_raise, pot, legal_actions):
        '''Equity-vs-pot-odds with range adjustment and consistent sizing.'''

        is_all_in = continue_cost >= my_stack

        # Apply equity discount based on opponent's bet size.
        equity = self._discount_equity(raw_equity, continue_cost, pot)

        pot_odds = continue_cost / (pot + continue_cost) if continue_cost > 0 else 0

        # --- All-in defense: tighter than the original (0.58 vs 0.55) ---
        if is_all_in:
            if equity >= 0.58:
                return CallAction() if CallAction in legal_actions else CheckAction()
            if CheckAction in legal_actions:
                return CheckAction()
            return FoldAction()

        # --- Strong hand (equity >= 0.6): raise with consistent sizing ---
        if equity >= 0.6 and RaiseAction in legal_actions:
            raise_amount = max(min_raise, int(pot * RAISE_POT_FRACTION))
            raise_amount = max(min_raise, min(raise_amount, max_raise))
            return RaiseAction(raise_amount)

        # --- Above pot odds + margin: call (no min-bet tells) ---
        if equity >= pot_odds + POT_ODDS_MARGIN:
            if CheckAction in legal_actions:
                return CheckAction()
            return CallAction()

        # --- Marginally above pot odds: still +EV to call ---
        if equity >= pot_odds:
            if CheckAction in legal_actions:
                return CheckAction()
            return CallAction()

        # --- Below pot odds: fold or check ---
        if CheckAction in legal_actions:
            return CheckAction()
        return FoldAction()


if __name__ == '__main__':
    run_bot(Player(), parse_args())
