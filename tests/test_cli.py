
def test_cli_main_help():
    """
    Run help for each modal CLI
    """
    from git_well import main
    modal = main.GitWellModalCLI()

    try:
        modal.run(argv=['--help'])
    except SystemExit:
        ...

    sub_commands = [c.__command__ for c in modal.sub_clis]
    for command in sub_commands:
        try:
            modal.run(argv=[command, '--help'])
        except SystemExit:
            ...
