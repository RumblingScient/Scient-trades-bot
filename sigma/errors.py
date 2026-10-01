"""sigma.errors - user-facing validation errors.

UserError: raised by the service layer when the *input* is wrong. The command / button layer
turns it into one short ephemeral line. Anything else that escapes is an internal error and goes
through the central handler (Phase 3 extends this with E-codes + ops-channel traces).
"""


class UserError(Exception):
    """Plain, actionable message for the person who ran the command. Never a stack trace."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
