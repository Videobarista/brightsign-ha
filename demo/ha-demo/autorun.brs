' Home Assistant demo presentation for BrightSign players
' ---------------------------------------------------------
' Makes a player offer everything the Home Assistant BrightSign integration can pick up from
' a presentation:
'   - Local Web Server on port 8080 (GetID, GetUserVars, SetValues, GetUDPEvents):
'     autorun version sensor, a text entity per user variable, a button per UDP command
'   - mDNS advertisement (_http._tcp): automatic discovery in Home Assistant
'   - UDP messages to Home Assistant (port 5000): UDP message event, Last UDP message sensor
'   - UDP commands from Home Assistant (port 5000), also through brightsign.send_command
'
' Use: copy this file as "autorun.brs" to the root of a SPARE SD card (it replaces any
' presentation on that card) and boot the player. Then, in Home Assistant, choose
' Reconfigure on the player (or add it), so the integration finds the web server.
'
' Network: by default the demo switches the player's wired network to DHCP (once; the
' setting is kept). Older sync scripts often leave a fixed address such as 192.168.1.10
' behind, which makes the player unreachable on another network. Set useDhcp = false to
' keep the player's current network settings.
'
' Commands (UDP to port 5000, the buttons in Home Assistant, or brightsign.send_command):
'   play, pause, stop, next, previous, ping, set:<variable>=<value>
' Messages to Home Assistant all start with "bs:":
'   bs:start, bs:state:<state>, bs:item:<n>, bs:var:<name>=<value>, bs:pong,
'   bs:heartbeat:<n> (every 60 seconds), bs:unknown:<command>

Sub Main()
    ' Where messages go: "255.255.255.255" (the whole network) or the IP address of Home Assistant
    haAddress = "255.255.255.255"
    m.udpPort = 5000
    webPort = 8080
    ' Switch the wired network to DHCP (see the note at the top)
    useDhcp = true

    if useDhcp then UseDhcpNetwork()

    msgPort = CreateObject("roMessagePort")

    info = CreateObject("roDeviceInfo")
    m.serial = info.GetDeviceUniqueId()
    m.model = info.GetModel()
    m.firmware = info.GetVersion()
    registry = CreateObject("roRegistrySection", "networking")
    m.unitName = registry.Read("un")
    if m.unitName = "" then m.unitName = "BrightSign demo"

    m.varNames = ["Title", "Message", "Mode"]
    m.varValues = ["Welcome", "Hello from Home Assistant", "demo"]
    m.state = "playing"
    m.item = 1
    m.lastCommand = "-"
    m.beats = 0

    m.sender = CreateObject("roDatagramSender")
    m.sender.SetDestination(haAddress, m.udpPort)

    receiver = CreateObject("roDatagramReceiver", m.udpPort)
    receiver.SetPort(msgPort)

    server = CreateObject("roHttpServer", { port: webPort })
    server.SetPort(msgPort)
    server.AddGetFromEvent({ url_path: "/GetID", user_data: "GetID" })
    server.AddGetFromEvent({ url_path: "/GetUserVars", user_data: "GetUserVars" })
    server.AddGetFromEvent({ url_path: "/GetUDPEvents", user_data: "GetUDPEvents" })
    server.AddPostToFormData({ url_path: "/SetValues", user_data: "SetValues" })

    props = CreateObject("roAssociativeArray")
    props.AddReplace("name", "BrightSign " + m.serial)
    props.AddReplace("type", "_http._tcp")
    props.AddReplace("port", webPort)
    props.AddReplace("_serialnumber", m.serial)
    props.AddReplace("_unitname", m.unitName)
    advert = CreateObject("roNetworkAdvertisement", props)
    if type(advert) <> "roNetworkAdvertisement" then print "mDNS advertisement not available"

    timer = CreateObject("roTimer")
    timer.SetPort(msgPort)
    timer.SetElapsed(60, 0)
    timer.Start()

    videoMode = CreateObject("roVideoMode")
    rect = CreateObject("roRectangle", 0, 0, videoMode.GetResX(), videoMode.GetResY())
    m.text = CreateObject("roTextWidget", rect, 10, 2, { Alignment: 1 })
    m.text.Show()

    SendMessage("bs:start")
    ShowStatus()

    while true
        msg = wait(0, msgPort)
        if type(msg) = "roHttpEvent" then
            HandleHttp(msg)
        else if type(msg) = "roDatagramEvent" then
            HandleCommand(msg.GetString())
        else if type(msg) = "roTimerEvent" then
            m.beats = m.beats + 1
            SendMessage("bs:heartbeat:" + StrI(m.beats).Trim())
            timer.Start()
        end if
    end while
End Sub


Sub HandleHttp(request As Object)
    page = request.GetUserData()

    if page = "SetValues" then
        form = request.GetFormData()
        if form <> invalid then
            for each key in form
                index = VarIndex(key)
                if index >= 0 then
                    m.varValues[index] = Left(form.Lookup(key), 255)
                    SendMessage("bs:var:" + m.varNames[index] + "=" + m.varValues[index])
                end if
            end for
        end if
        ShowStatus()
        request.SetResponseBodyString("OK")
        request.SendResponse(200)
        return
    end if

    port = StrI(m.udpPort).Trim()
    if page = "GetID" then
        body = "<BrightSignID>"
        body = body + XmlTag("unitName", m.unitName)
        body = body + XmlTag("unitDescription", "Home Assistant demo")
        body = body + XmlTag("serialNumber", m.serial)
        body = body + XmlTag("functionality", "standalone")
        body = body + XmlTag("autorunVersion", "ha-demo 1.0")
        body = body + XmlTag("firmwareVersion", m.firmware)
        body = body + "</BrightSignID>"
    else if page = "GetUserVars" then
        body = "<BrightSignUserVariables>"
        for i = 0 to m.varNames.Count() - 1
            body = body + "<BrightSignVar name=" + Chr(34) + XmlEscape(m.varNames[i]) + Chr(34) + ">"
            body = body + XmlEscape(m.varValues[i]) + "</BrightSignVar>"
        end for
        body = body + "</BrightSignUserVariables>"
    else
        body = "<udpEventsInfo>" + XmlTag("receivePort", port) + XmlTag("destinationPort", port)
        body = body + "<udpEvents>"
        commands = [["Play", "play"], ["Pause", "pause"], ["Stop", "stop"], ["Next", "next"], ["Previous", "previous"], ["Ping", "ping"]]
        for each command in commands
            body = body + "<udpEvent>" + XmlTag("label", command[0]) + XmlTag("action", command[1]) + "</udpEvent>"
        end for
        body = body + "</udpEvents></udpEventsInfo>"
    end if
    request.AddResponseHeader("Content-type", "text/xml; charset=utf-8")
    request.SetResponseBodyString(body)
    request.SendResponse(200)
End Sub


Sub HandleCommand(raw As String)
    command = raw.Trim()
    if Left(command, 3) = "bs:" then return  ' our own message, received back from the broadcast

    ' brightsign.send_command arrives as JSON from the player's REST API
    if Left(command, 1) = "{" then
        json = ParseJson(command)
        if type(json) = "roAssociativeArray" and json.DoesExist("command") then
            command = json.Lookup("command")
        end if
    end if
    if type(command) <> "roString" and type(command) <> "String" then return

    m.lastCommand = Left(command, 64)
    lower = LCase(command)
    if lower = "play" then
        m.state = "playing"
        SendMessage("bs:state:playing")
    else if lower = "pause" then
        m.state = "paused"
        SendMessage("bs:state:paused")
    else if lower = "stop" then
        m.state = "stopped"
        SendMessage("bs:state:stopped")
    else if lower = "next" then
        m.item = m.item + 1
        if m.item > 5 then m.item = 1
        m.state = "playing"
        SendMessage("bs:item:" + StrI(m.item).Trim())
    else if lower = "previous" then
        m.item = m.item - 1
        if m.item < 1 then m.item = 5
        m.state = "playing"
        SendMessage("bs:item:" + StrI(m.item).Trim())
    else if lower = "ping" then
        SendMessage("bs:pong")
    else if Left(lower, 4) = "set:" then
        rest = Mid(command, 5)
        equals = Instr(1, rest, "=")
        index = -1
        if equals > 1 then index = VarIndex(Left(rest, equals - 1))
        if index >= 0 then
            m.varValues[index] = Left(Mid(rest, equals + 1), 255)
            SendMessage("bs:var:" + m.varNames[index] + "=" + m.varValues[index])
        else
            SendMessage("bs:unknown:" + m.lastCommand)
        end if
    else
        SendMessage("bs:unknown:" + m.lastCommand)
    end if
    ShowStatus()
End Sub


Sub UseDhcpNetwork()
    network = CreateObject("roNetworkConfiguration", 0)
    if type(network) <> "roNetworkConfiguration" then return
    current = network.GetCurrentConfig()
    if type(current) = "roAssociativeArray" then
        dhcp = current.Lookup("dhcp")
        if type(dhcp) = "roBoolean" or type(dhcp) = "Boolean" then
            if dhcp then return  ' already on DHCP
        end if
    end if
    print "Switching the wired network to DHCP"
    network.SetDHCP()
    network.Apply()
    sleep(5000)  ' give DHCP a moment before the web server and UDP start
End Sub


Sub SendMessage(message As String)
    m.sender.Send(message)
End Sub


Sub ShowStatus()
    m.text.Clear()
    m.text.PushString("Home Assistant demo - " + m.unitName)
    m.text.PushString(m.model + "   " + m.serial)
    m.text.PushString("State: " + m.state + "   Item: " + StrI(m.item).Trim())
    for i = 0 to m.varNames.Count() - 1
        m.text.PushString(m.varNames[i] + ": " + m.varValues[i])
    end for
    m.text.PushString("Last command: " + m.lastCommand)
End Sub


Function VarIndex(name As String) As Integer
    for i = 0 to m.varNames.Count() - 1
        if LCase(m.varNames[i]) = LCase(name) then return i
    end for
    return -1
End Function


Function XmlTag(name As String, value As String) As String
    return "<" + name + ">" + XmlEscape(value) + "</" + name + ">"
End Function


Function XmlEscape(value As String) As String
    value = CreateObject("roRegex", "&", "").ReplaceAll(value, "&amp;")
    value = CreateObject("roRegex", "<", "").ReplaceAll(value, "&lt;")
    value = CreateObject("roRegex", ">", "").ReplaceAll(value, "&gt;")
    value = CreateObject("roRegex", Chr(34), "").ReplaceAll(value, "&quot;")
    return value
End Function
