MODEL="gemini-2.5-flash"

WORKSPACE_RULE = """

If the user message starts with a [Workspace] block, use those exact file paths and never ask
the user for paths. Write any generated file into the listed output directory."""