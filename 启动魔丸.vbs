' Launcher for the desktop pet ("Mowan").
' Double-click this (or its desktop shortcut) to start everything:
'   Electron app opens the pet window + the console window, and the app itself
'   spawns the Python services (bus broker / brain / dialogue / screen capture).
' Window style 0 = hidden, so no black console box stays on screen.
'
' Kept ASCII-only on purpose: VBScript is picky about file encoding, and any
' non-ASCII literal here risks breaking on a different system codepage. All
' paths are resolved at runtime from this script's own location, so nothing
' needs to be hard-coded even though the repo path contains Chinese characters.

Option Explicit

Dim fso, sh, root, appDir, electron, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

root     = fso.GetParentFolderName(WScript.ScriptFullName)
appDir   = fso.BuildPath(root, "apps\character")
electron = fso.BuildPath(appDir, "node_modules\.bin\electron.cmd")

If Not fso.FileExists(electron) Then
  MsgBox "Electron not found:" & vbCrLf & electron & vbCrLf & vbCrLf & _
         "Run 'npm install' inside apps\character first.", 16, "Mowan"
  WScript.Quit 1
End If

sh.CurrentDirectory = appDir
cmd = """" & electron & """ ."
sh.Run cmd, 0, False
