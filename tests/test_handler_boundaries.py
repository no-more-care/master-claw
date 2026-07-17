from masterclaw.app.handlers.commands import CommandHandlers
from masterclaw.app.handlers.dispatching import DispatchExecution
from masterclaw.app.handlers.information import InformationHandlers
from masterclaw.app.handlers.play import PlayHandlers
from masterclaw.app.handlers.preparation import PreparationHandlers
from masterclaw.app.handlers.support import HandlerSupport
from masterclaw.app.handlers.world_management import WorldManagementHandlers
from masterclaw.app.message_handler import MessageApplication


def test_message_application_is_a_thin_handler_facade() -> None:
    assert (
        set(MessageApplication.__dict__)
        & {
            "_dispatch",
            "_handle_command",
            "_handle_action_declaration",
            "_handle_natural_world_creation",
        }
        == set()
    )
    assert MessageApplication._dispatch is DispatchExecution._dispatch
    assert MessageApplication._handle_command is CommandHandlers._handle_command
    assert (
        MessageApplication._handle_natural_world_creation
        is WorldManagementHandlers._handle_natural_world_creation
    )
    assert (
        MessageApplication._handle_natural_character_creation
        is PreparationHandlers._handle_natural_character_creation
    )
    assert MessageApplication._character_status is InformationHandlers._character_status
    assert MessageApplication._handle_action_declaration is PlayHandlers._handle_action_declaration
    assert MessageApplication._assemble_context is HandlerSupport._assemble_context
