import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Mimarchy's bar widget: one icon, one panel.
//
// Everything with a decision in it lives in `mimarchy-ctl`, not here. Plugins
// run unsandboxed inside the user's long-lived shell process, so a crash in
// this file is a crash in their desktop — and QML cannot be unit tested from
// this repo at all. What is left here is deliberately declarative: read a JSON
// document, draw it, and hand every interaction back to a subprocess.
//
// State arrives by two routes, for two different reasons:
//
//   * The lighting state file is *watched*. Effect, speed and link changes made
//     in the TUI show up here within a frame, with no polling and no process.
//   * Everything else — sensors, and whether the two units are running — needs
//     `mimarchy-ctl status --json`, so it is polled: quickly while the panel is
//     open, slowly while it is closed, since a shut panel only needs the icon
//     to notice the daemon stopping.
//
// The TUI stays the full control surface. This is the glanceable 90% plus the
// two adjustments worth having without opening a window.
Panel {
  id: root
  moduleName: "io.github.villenull.mimarchy"
  ipcTarget: "mimarchy"

  // ---- state -------------------------------------------------------------

  // The parsed `mimarchy-ctl status --json` document, or null before the first
  // successful read. Null is meaningfully different from "everything is off":
  // it is what "the backend is not installed" looks like, and the panel says so
  // rather than drawing a confident row of zeroes.
  property var status: null
  property bool backendMissing: false
  property real wheelAccumulator: 0

  readonly property bool lightingActive: status ? status.lighting_active === true : false
  readonly property bool displayActive: status ? status.display_active === true : false
  readonly property bool linked: status ? status.linked === true : true
  readonly property int speedStops: status && status.speed_stops ? status.speed_stops : 5

  readonly property var targetKeys: {
    if (!status || !status.targets) return []
    return Object.keys(status.targets)
  }

  // Linked means the pair is driven as one, so naming both on one row is the
  // honest summary; unlinked, each target speaks for itself.
  readonly property string effectSummary: {
    if (!status || targetKeys.length === 0) return "—"
    var first = status.targets[targetKeys[0]]
    if (!first) return "—"
    if (linked || targetKeys.length === 1) return first.effect
    var effects = targetKeys.map(function (k) { return status.targets[k].effect })
    var same = effects.every(function (e) { return e === effects[0] })
    return same ? effects[0] : effects.join(" / ")
  }

  readonly property bool anyTargetTakesSpeed: {
    for (var i = 0; i < targetKeys.length; i++)
      if (status.targets[targetKeys[i]].takes_speed) return true
    return false
  }

  // ---- settings ----------------------------------------------------------

  readonly property bool showSensorsInTooltip: setting("showSensorsInTooltip", true)
  readonly property int pollIntervalSec: Math.max(1, setting("pollIntervalSec", 2))
  readonly property int idlePollIntervalSec: Math.max(5, setting("idlePollIntervalSec", 30))

  // ---- formatting --------------------------------------------------------

  function formatTemp(value) {
    return (value === null || value === undefined) ? "—" : Math.round(value) + "°"
  }

  function formatRpm(value) {
    return (value === null || value === undefined) ? "—" : Math.round(value) + " rpm"
  }

  function speedText(target) {
    if (!target || !target.takes_speed) return "no speed"
    return "speed " + target.speed_stop + "/" + speedStops
  }

  readonly property string tooltip: {
    if (backendMissing) return "Mimarchy — backend not installed"
    if (!status) return "Mimarchy"

    var parts = ["Mimarchy — " + effectSummary]
    if (!lightingActive) parts.push("lighting stopped")
    if (showSensorsInTooltip && status.sensors) {
      var s = status.sensors
      var readings = []
      if (s.cpu_temp !== null && s.cpu_temp !== undefined) readings.push("cpu " + formatTemp(s.cpu_temp))
      if (s.gpu_temp !== null && s.gpu_temp !== undefined) readings.push("gpu " + formatTemp(s.gpu_temp))
      if (readings.length > 0) parts.push(readings.join("  "))
    }
    return parts.join("\n")
  }

  // ---- reading state -----------------------------------------------------

  // Matches lightstate.STATE_PATH: XDG_RUNTIME_DIR, falling back to the config
  // home exactly as the Python side does, so the widget watches the same file
  // the daemon reads rather than a second guess at where it lives.
  readonly property string runtimeDir: Quickshell.env("XDG_RUNTIME_DIR") || ""
  readonly property string configHome: Quickshell.env("XDG_CONFIG_HOME")
    || (Quickshell.env("HOME") + "/.config")
  readonly property string statePath: (runtimeDir !== "" ? runtimeDir : configHome)
    + "/mimarchy-lighting.json"

  // Watched rather than polled. A keypress in the TUI writes this file
  // atomically, so the bar follows the TUI without either knowing about the
  // other. The contents are not parsed here — the file changing is only used as
  // a signal to re-run status, which is the one place the shape is understood.
  FileView {
    path: root.statePath
    watchChanges: true
    printErrors: false
    onFileChanged: root.refresh()
    onLoaded: root.refresh()
  }

  Process {
    id: statusProcess
    running: false
    command: ["mimarchy-ctl", "status", "--json"]

    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var parsed = JSON.parse(String(text || ""))
          root.status = (parsed && typeof parsed === "object") ? parsed : null
          root.backendMissing = false
        } catch (e) {
          // Malformed output is not the same as a missing backend, and is not
          // worth clearing a good previous reading over: keep what is on screen
          // and try again on the next tick.
          console.warn("mimarchy", "could not parse status", e)
        }
      }
    }

    onExited: function (exitCode) {
      // 127 is the shell's "command not found"; Quickshell surfaces a failure
      // to launch the same way. Either means the Python side was never
      // installed, which is a supported half-state — `omarchy plugin add`
      // deliberately runs no install hooks, so the widget can arrive first.
      if (exitCode !== 0 && !root.status) root.backendMissing = true
    }
  }

  function refresh() {
    if (!statusProcess.running) statusProcess.running = true
  }

  // Actions are serialised rather than run concurrently, because two
  // `mimarchy-ctl` writes racing each other is exactly the interleaving the
  // atomic write is there to prevent — and a second one would read the state
  // before the first had written it.
  property var pendingAction: null

  Process {
    id: actionProcess
    running: false

    onExited: {
      // Re-read immediately rather than waiting for the next tick, so a click
      // feels like it did something. The state-file watch would catch lighting
      // changes anyway; this is what makes the display toggle prompt as well.
      root.refresh()

      // Dropping a click that arrived mid-flight is worse than delaying it: on
      // a toggle it shows as the switch flipping and snapping back. Only the
      // most recent is kept — a burst of scroll steps wants to end where the
      // user stopped, not replay every notch.
      if (root.pendingAction) {
        var next = root.pendingAction
        root.pendingAction = null
        root.run(next)
      }
    }
  }

  function run(args) {
    if (actionProcess.running) {
      root.pendingAction = args
      return
    }
    actionProcess.command = ["mimarchy-ctl"].concat(args)
    actionProcess.running = true
  }

  // Closed, this only has to notice the lighting daemon stopping, which is not
  // a per-second question. Open, it is drawing live temperatures.
  Timer {
    interval: (root.opened ? root.pollIntervalSec : root.idlePollIntervalSec) * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  onOpenedChanged: if (opened) refresh()

  // ---- bar icon ----------------------------------------------------------

  // U+F0335 (nf-md-lightbulb), the same glyph the Waybar module used. Renders
  // through the bar's own family: Omarchy sets that to `monospace`, which
  // fontconfig aliases to the user's Nerd Font, so this follows `omarchy font
  // set` rather than pinning a family the user did not choose.
  readonly property string glyph: "󰌵"

  // Dimmed when the LEDs are frozen — either the daemon is stopped or the
  // effect is `off`. That is the one piece of state worth reading from across
  // the room, so it is the one the icon spends its only channel on.
  readonly property bool lightsLive: lightingActive && effectSummary !== "off"
  readonly property color iconColor: backendMissing
    ? Qt.darker(barForeground, 2.0)
    : (lightsLive ? barForeground : Qt.darker(barForeground, 1.55))

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: root.tooltip

    iconComponent: Component {
      Item {
        OpticalGlyph {
          anchors.centerIn: parent
          text: root.glyph
          fontFamily: Style.font.family
          fontSize: Style.space(12)
          color: root.iconColor
        }
      }
    }

    onPressed: function (buttonCode) {
      if (buttonCode === Qt.RightButton) root.run(["display", "toggle"])
      else if (buttonCode === Qt.MiddleButton) root.run(["link", "toggle"])
      else root.toggle()
    }

    // Accumulated rather than acted on per event, because a touchpad sends many
    // sub-notch deltas per gesture and one step per event would run the ladder
    // end to end on a single swipe.
    onWheelMoved: function (delta) {
      if (!root.anyTargetTakesSpeed) return
      var wheel = Util.wheelSteps(root.wheelAccumulator, delta)
      root.wheelAccumulator = wheel.remainder
      if (wheel.steps === 0) return
      root.run(["speed", wheel.steps > 0 ? "+" : "-"])
    }
  }

  // ---- panel -------------------------------------------------------------

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(300))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(420))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function (direction) { root.switchPanel(direction) }
      onTextKey: function (t) {
        var key = String(t || "").toLowerCase()
        // The same letters the TUI uses, so muscle memory carries over.
        if (key === "d") root.run(["display", "toggle"])
        else if (key === "u") root.run(["link", "toggle"])
        else if (key === "+" || key === "=") root.run(["speed", "+"])
        else if (key === "-") root.run(["speed", "-"])
      }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        spacing: Style.space(8)

        PanelSectionHeader {
          width: parent.width
          text: "Lighting"
        }

        // The backend-missing case gets a sentence rather than an empty panel:
        // a plugin installed before its Python side is a normal state to be in
        // for a minute, and the fix is one command.
        Text {
          width: parent.width
          visible: root.backendMissing
          text: "mimarchy-ctl not found.\nRun install.sh from the Mimarchy repo."
          wrapMode: Text.WordWrap
          color: Color.popups.text
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }

        Repeater {
          model: root.backendMissing ? [] : root.targetKeys

          Row {
            required property string modelData
            // Guarded rather than assumed: the model is empty whenever `status`
            // is null, so this cannot currently fire — but a row that throws
            // during a poll would take the shell's render loop with it, and
            // that is not a bet worth winning by inspection.
            readonly property var target: (root.status && root.status.targets)
              ? root.status.targets[modelData] : null

            width: column.width
            spacing: Style.space(8)

            Text {
              width: Math.round(column.width * 0.42)
              text: modelData.replace(/_/g, " ")
              color: Color.popups.text
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              elide: Text.ElideRight
            }

            Text {
              text: parent.target ? parent.target.effect : "—"
              color: Color.accent
              font.family: Style.font.family
              font.pixelSize: Style.font.body
            }

            Text {
              text: root.speedText(parent.target)
              color: Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }

        PanelSeparator { width: parent.width; visible: !root.backendMissing }

        Text {
          width: parent.width
          visible: !root.backendMissing
          text: {
            if (!root.status || !root.status.sensors) return ""
            var s = root.status.sensors
            return "cpu " + root.formatTemp(s.cpu_temp)
              + "   gpu " + root.formatTemp(s.gpu_temp)
              + "   fan " + root.formatRpm(s.cpu_fan_rpm)
          }
          color: Color.popups.text
          font.family: Style.font.family
          font.pixelSize: Style.font.body
        }

        Text {
          width: parent.width
          visible: !root.backendMissing && !root.lightingActive
          text: "Lighting daemon stopped — LEDs frozen."
          wrapMode: Text.WordWrap
          color: Color.urgent
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }

        PanelSeparator { width: parent.width; visible: !root.backendMissing }

        // Toggles rather than buttons, because both are boolean states the
        // panel is already reporting — a control that shows its own value beats
        // a button whose label has to spell the value out.
        Toggle {
          width: parent.width
          visible: !root.backendMissing
          label: "Cooler display"
          // Worth saying plainly: the panel has no off command in its protocol,
          // so "off" means the telemetry stream stopped and the display blanks
          // itself about fifty seconds later.
          description: root.displayActive ? "streaming telemetry" : "blanks ~50s after stopping"
          checked: root.displayActive
          onClicked: root.run(["display", "toggle"])
        }

        Toggle {
          width: parent.width
          visible: !root.backendMissing
          label: "Link CPU and GPU"
          description: root.linked ? "driven together" : "driven independently"
          checked: root.linked
          onClicked: root.run(["link", "toggle"])
        }

        Button {
          width: parent.width
          text: "Open Mimarchy"
          bordered: true
          onClicked: {
            root.close()
            if (root.bar) root.bar.run("omarchy-launch-mimarchy")
          }
        }
      }
    }
  }
}
