'''
Baseline bot: always applies maximum pressure (all-in when possible).
'''
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "python_skeleton"))

from skeleton.actions import CallAction, CheckAction, RaiseAction, RedrawAction
from skeleton.bot import Bot
from skeleton.runner import parse_args, run_bot


class Player(Bot):
    def handle_new_round(self, game_state, round_state, active):
        _ = game_state
        _ = round_state
        _ = active

    def handle_round_over(self, game_state, terminal_state, active):
        _ = game_state
        _ = terminal_state
        _ = active

    def get_action(self, game_state, round_state, active):
        _ = game_state
        legal_actions = round_state.legal_actions()

        def aggressive_action():
            if RaiseAction in legal_actions:
                _, max_raise = round_state.raise_bounds()
                return RaiseAction(max_raise)
            if CallAction in legal_actions:
                return CallAction()
            return CheckAction()

        if RedrawAction in legal_actions and round_state.street in (3, 4):
            # Redraw first hole card before committing chips.
            return RedrawAction("hole", 0, aggressive_action())

        return aggressive_action()


if __name__ == "__main__":
    run_bot(Player(), parse_args())
