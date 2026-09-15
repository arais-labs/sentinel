# Sentinel Frontend

React UI for the Sentinel desktop app.

From the repository root:

```bash
make setup
make dev
```

Electron opens the UI from the electron-vite development URL with hot reload.
Packaged builds load `sentinel://app`. API requests and streams go through the
desktop bridge to FastAPI over a private Unix socket. There is no Sentinel login;
the app opens to Instances in App Settings. See [Contributing](../../../CONTRIBUTING.md) for setup and checks.

Opening an instance shows its tiling workspace. Click a sidebar page to replace
the focused pane, or drag it onto a pane edge to split the workspace. Drag pane
headers to rearrange views and drag dividers to resize them. Layouts are saved
across reloads. Desktop, Terminal, and Files are independent panes that follow
the selected session and share its desktop IPC stream.

The top-right settings control opens App Settings and returns to the workspace
when closed; instance management stays in that menu.

To build or type-check only the frontend:

```bash
npm --prefix apps/frontend/sentinel run build
```
