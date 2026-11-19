' BrightSign UDP demo for the Home Assistant BrightSign integration.
'
' Copy this file as "autorun.brs" to the root of an EMPTY (spare) SD card and boot a
' player with it. It replaces any presentation on that card. The player then:
'   - sends "demo:start" once and "demo:heartbeat:<n>" every 10 seconds
'     (UDP broadcast to port 5000, picked up by the integration as UDP messages)
'   - answers every UDP message it receives on port 5001 with "demo:ack:<message>"
'     (test with the brightsign.send_udp action, port 5001)
'   - shows the last received message on screen

Sub Main()
    port = CreateObject("roMessagePort")

    sender = CreateObject("roDatagramSender")
    sender.SetDestination("255.255.255.255", 5000)

    receiver = CreateObject("roDatagramReceiver", 5001)
    receiver.SetPort(port)

    timer = CreateObject("roTimer")
    timer.SetPort(port)
    timer.SetElapsed(10, 0)
    timer.Start()

    videoMode = CreateObject("roVideoMode")
    rect = CreateObject("roRectangle", 0, 0, videoMode.GetResX(), videoMode.GetResY())
    text = CreateObject("roTextWidget", rect, 3, 2, { Alignment: 1 })
    text.PushString("BrightSign UDP demo - send UDP to port 5001")
    text.Show()

    count = 0
    sender.Send("demo:start")

    while true
        msg = wait(0, port)
        if type(msg) = "roTimerEvent" then
            count = count + 1
            sender.Send("demo:heartbeat:" + StrI(count).Trim())
            timer.Start()
        else if type(msg) = "roDatagramEvent" then
            received = msg.GetString()
            sender.Send("demo:ack:" + received)
            text.Clear()
            text.PushString("Received: " + received)
        end if
    end while
End Sub
