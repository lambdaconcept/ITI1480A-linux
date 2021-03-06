#!/usr/bin/env python
import os
import wx
import threading
import signal
import subprocess
import sys
from cStringIO import StringIO
from collections import deque

# TODO: use wxWidget 2.9 wxTreeCtrl
from wx.gizmos import TreeListCtrl

from gui import wxITI1480AMainFrame
from iti1480a.parser import tic_to_time, short_tic_to_time, \
    ReorderedStream, MESSAGE_RAW, MESSAGE_RESET, MESSAGE_TRANSACTION, \
    decode, TOKEN_TYPE_ACK, TOKEN_TYPE_NAK, TOKEN_TYPE_STALL, \
    TOKEN_TYPE_NYET, Packetiser, TransactionAggregator, PipeAggregator, \
    Endpoint0TransferAggregator, MESSAGE_TRANSFER, ParsingDone, \
    TOKEN_TYPE_PRE_ERR, BaseAggregator, MESSAGE_TRANSACTION_ERROR, \
    MESSAGE_TRANSFER_ERROR

def maybeCallAfter(func, *args, **kw):
    if wx.Thread_IsMain():
        func(*args, **kw)
    else:
        wx.CallAfter(func, *args, **kw)

class Capture(object):
    _subprocess = None
    _open_thread = None
    paused = False
    data = None

    def __init__(self, callback):
        self._callback = callback

    def start(self):
        # TODO: unhardcode paths and make them portable.
        self._subprocess = subprocess.Popen([
            sys.executable, '-m', 'iti1480a.capture', '-f', '/lib/firmware/ITI1480A.rbf', '-v'],
            stdout=subprocess.PIPE,
        )
        self._open_thread = read_thread = threading.Thread(
            target=self._callback,
            args=(self._read, ), kwargs={'read_buf': 16})
        read_thread.daemon = True
        # XXX: should probably rather use an on-disk temp file...
        self.data = StringIO()
        read_thread.start()

    def _read(self, size):
        subprocess = self._subprocess
        if subprocess is None:
            return ''
        data = subprocess.stdout.read(size)
        self.data.write(data)
        return data

    def pause(self):
        self.paused = True
        self._subprocess.send_signal(signal.SIGTSTP)

    def cont(self):
        self._subprocess.send_signal(signal.SIGCONT)
        self.paused = False

    def stop(self):
        self._subprocess.kill()
        # Safe from deadlocks, because subprocess stdout read is done in
        # another thread.
        self._subprocess.wait()
        self._open_thread = self._subprocess = None

class EventListManagerBase(BaseAggregator):
    # XXX: horrible API
    def __init__(self, app, device, endpoint, addBaseTreeItem, event_list=None):
        self._pipe = (device, endpoint)
        self._app = app
        self._event_list = event_list
        self.__addBaseTreeItem = addBaseTreeItem

    def _addBaseTreeItem(self, *args, **kw):
        if self._event_list is None:
            maybeCallAfter(self._realAddBaseTreeItem, args, kw)
        else:
            self._realAddBaseTreeItem(args, kw)

    def _realAddBaseTreeItem(self, args, kw):
        if self._event_list is None:
            self._event_list = self._app.getPipeEventList(*self._pipe)
        self.__addBaseTreeItem(self._event_list, *args, **kw)

    def push(self, tic, transaction_type, data):
        raise NotImplementedError

class HubEventListManager(EventListManagerBase):
    def push(self, tic, transaction_type, data):
        pass

class EndpointEventListManager(EventListManagerBase):
    def push(self, tic, transaction_type, data):
        is_error = transaction_type in (MESSAGE_TRANSFER_ERROR, MESSAGE_TRANSACTION_ERROR)
        if is_error:
            caption, data =  data
        if transaction_type in (MESSAGE_TRANSFER, MESSAGE_TRANSFER_ERROR):
            _decode = self._decode
            child_list = []
            append = child_list.append
            for _, packets in data:
                append(_decode(packets))
        elif transaction_type in (MESSAGE_TRANSACTION,
                MESSAGE_TRANSACTION_ERROR):
            child_list = [self._decode(data)]
        first_child = child_list[0]
        device, endpoint, interface, _, speed, payload = first_child[1]
        if is_error:
            status = 'Incomplete'
        else:
            caption = first_child[0]
            status = child_list[-1][1][3]
        self._addBaseTreeItem(caption, (device, endpoint, interface, status, speed, payload), first_child[2], child_list)

    @staticmethod
    def _decode(packets):
        decoded = [decode(x) for x in packets]
        if packets[0][0] == TOKEN_TYPE_PRE_ERR:
            start = decoded[1]
        else:
            start = decoded[0]
        interface = '' # TODO
        handshake = decoded[-1]
        if handshake['name'] in (TOKEN_TYPE_ACK, TOKEN_TYPE_NAK,
                TOKEN_TYPE_STALL, TOKEN_TYPE_NYET):
            status = handshake['name']
        else:
            status = ''
        speed = '' # TODO (LS/FS/HS)
        payload = ''
        for item in decoded:
            if 'data' in item:
                payload += (' '.join('%02x' % (ord(x), )
                    for x in item['data']))
        return (start['name'], (str(start['address']), str(
            start['endpoint']), interface, status, speed, payload), start['tic'], (
            (x['name'], ('', '', '', '', '',
                ' '.join('%02x' % (ord(y), ) for y in x.get('data', ''))
                ), x['tic'], ()) for x in decoded
        ))

CHUNK_SIZE = 16 * 1024
class ITI1480AMainFrame(wxITI1480AMainFrame):
    _statusbar_size_changed = False

    def __init__(self, *args, **kw):
        loadfile = kw.pop('loadfile', None)
        cwd = os.getcwd()
        os.chdir(os.path.dirname(__file__))
        super(ITI1480AMainFrame, self).__init__(*args, **kw)
        os.chdir(cwd)
        self._openDialog = wx.FileDialog(self, 'Choose a file', '', '',
            '*.usb', wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        self._saveDialog = wx.FileDialog(self, 'File to save as', '', '',
            '*.usb', wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        self._enableSave(False)
        self._enableCapture(True)
        image_size = (16, 16)
        self.image_list = image_list = wx.ImageList(*image_size)
        self._folderClosed = image_list.Add(wx.ArtProvider_GetBitmap(
            wx.ART_FOLDER, wx.ART_OTHER, image_size))
        self._folderOpened = image_list.Add(wx.ArtProvider_GetBitmap(
            wx.ART_FILE_OPEN, wx.ART_OTHER, image_size))
        self._file = image_list.Add(wx.ArtProvider_GetBitmap(
            wx.ART_NORMAL_FILE, wx.ART_OTHER, image_size))
        self.load_gauge = gauge = wx.Gauge(self.statusbar,
            style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
        gauge.Show(False)
        self.statusbar.Bind(wx.EVT_SIZE, self.onResizeStatusbar)
        self.statusbar.Bind(wx.EVT_IDLE, self.onIdleStatusbar)
        self._repositionGauge()
        self._capture = Capture(self._openFile)
        self._device_dict = {}
        self._initEventList(self.capture_list)
        self._initEventList(self.bus_list)
        self._initEventList(self.error_list)
        if loadfile is not None:
            self.openFile(loadfile)

    def _enableCapture(self, enable):
        self._enableId(4, enable) # Start
        self._enableId(5, not enable) # Pause/continue
        self._enableId(wx.ID_STOP, not enable)

    def _enableSave(self, enable):
        self._enableId(wx.ID_SAVE, enable)

    def _enableId(self, ident, enable):
        self.menubar.Enable(ident, enable)
        self.toolbar.EnableTool(ident, enable)

    def _newHub(self, device):
        event_list = self._newEventList(self.device_notebook)
        self.device_notebook.AddPage(event_list, 'Hub %i' % (device, ))
        self._device_dict[endpoint] = event_list

    def _newEndpoint(self, device, endpoint):
        assert wx.Thread_IsMain()
        try:
            endpoint_notebook, endpoint_dict = self._device_dict[device]
        except KeyError:
            create_device = True
            endpoint_dict = {}
            endpoint_notebook = wx.Notebook(self.device_notebook, -1, style=0)
            self._device_dict[device] = (endpoint_notebook, endpoint_dict)
        else:
            create_device = False
        endpoint_dict[endpoint] = event_list = self._newEventList(
            endpoint_notebook)
        endpoint_notebook.AddPage(event_list, 'Ep %i' % (endpoint, ))
        if create_device:
            self.device_notebook.AddPage(
                endpoint_notebook,
                'Device %i' % (device, ),
            )

    def getPipeEventList(self, device, endpoint):
        event_list = self._device_dict[device]
        if endpoint is not None:
            event_list = event_list[1][endpoint]
        return event_list

    def _initEventList(self, tree):
        for column_name, width in [
                    ('Time (min:sec.ms\'us"ns)', 140),
                    ('Item', 170),
                    ('Device', 40),
                    ('Endpoint', 40),
                    ('Interface', 40),
                    ('Status', 40),
                    ('Speed', 40),
                    ('Payload', 300),
                ]:
            tree.AddColumn(column_name, width=width)
        tree.SetMainColumn(1)
        tree.AddRoot('')
        tree.SetImageList(self.image_list)

    def _newEventList(self, parent):
        tree = TreeListCtrl(parent, -1, style=wx.TR_HIDE_ROOT | wx.TR_NO_BUTTONS | wx.TR_ROW_LINES | wx.TR_FULL_ROW_HIGHLIGHT)
        self._initEventList(tree)
        return tree

    def _repositionGauge(self):
        rect = self.statusbar.GetFieldRect(1)
        self.load_gauge.SetPosition((rect.x+2, rect.y+2))
        self.load_gauge.SetSize((rect.width-4, rect.height-4))

    def onResizeStatusbar(self, event):
        # XXX: see description of this trick in the wx StatusBar demo.
        self._statusbar_size_changed = True
        self._repositionGauge()

    def onIdleStatusbar(self, event):
        if self._statusbar_size_changed:
            self._repositionGauge()
            self._statusbar_size_changed = False

    def onExit(self, event):
        self.Close(True)

    def onStart(self, event):
        self._capture.start()
        self._enableCapture(False)

    def onStop(self, event):
        self._capture.stop()
        self._enableSave(True)
        self._enableCapture(True)

    def onPause(self, event):
        if self._capture.paused:
            self._capture.cont()
        else:
            self._capture.pause()

    def onSave(self, event):
        dialog = self._saveDialog
        if dialog.ShowModal() == wx.ID_OK:
            # XXX: very naive implementation.
            with open(dialog.GetPath(), 'w') as out:
                out.write(self._capture.data.getvalue())

    def onOpen(self, event):
        dialog = self._openDialog
        if dialog.ShowModal() == wx.ID_OK:
            self._enableSave(False)
            self.openFile(dialog.GetPath())

    def openFile(self, path):
        stream = open(path)
        gauge = self.load_gauge
        gauge.SetValue(0)
        stream.seek(0, 2)
        gauge.SetRange(stream.tell())
        stream.seek(0)
        gauge.Show(True)
        open_thread = threading.Thread(target=self._openFile,
            args=(stream.read, ), kwargs={'use_gauge': True})
        open_thread.daemon = True
        open_thread.start()

    def _openFile(self, read, use_gauge=False, read_buf=CHUNK_SIZE):
        def addTreeItem(parent, event_list, caption, data, absolute_tic,
                child_list):
            SetItemText = event_list.SetItemText
            SetItemImage = event_list.SetItemImage
            tree_item = event_list.AppendItem(parent, caption)
            SetItemText(tree_item, tic_to_time(absolute_tic), 0)
            for column, caption in enumerate(data, 2):
                SetItemText(tree_item, caption, column)
            if child_list:
                SetItemImage(tree_item, self._folderClosed,
                    which=wx.TreeItemIcon_Normal)
                SetItemImage(tree_item, self._folderOpened,
                    which=wx.TreeItemIcon_Expanded)
            else:
                SetItemImage(tree_item, self._file,
                    which=wx.TreeItemIcon_Normal)
            for (child_caption, child_data, child_absolute_tic,
                    grand_child_list) in child_list:
                addTreeItem(tree_item, event_list, child_caption, child_data,
                    child_absolute_tic, grand_child_list)
        tree_list = deque()

        def flushTreeList():
            pop = tree_list.popleft
            while True:
                try:
                    args, kw = pop()
                except IndexError:
                    break
                addTreeItem(args[0].GetRootItem(), *args, **kw)

        def addBaseTreeItem(*args, **kw):
            need_reschedule = not tree_list
            tree_list.append((args, kw))
            if need_reschedule:
                maybeCallAfter(flushTreeList)

        def captureEvent(tic, event_type, data):
            if event_type == MESSAGE_RAW:
                addBaseTreeItem(self.capture_list, data, (), tic, ())
            elif event_type == MESSAGE_RESET:
                addBaseTreeItem(self.bus_list, 'Reset (%s)' % (short_tic_to_time(data), ), (), tic, ())
            else:
                raise NotImplementedError(event_type)
        captureEvent.stop = lambda: None
        captureEvent.push = captureEvent

        def busEvent(tic, event_type, data):
            assert event_type == MESSAGE_TRANSACTION, event_type
            assert len(data) == 1, data
            addBaseTreeItem(self.bus_list, 'SOF %i' % (decode(data[0])['frame'], ), (), tic, ())
        busEvent.stop = lambda: None
        busEvent.push = busEvent

        def newHub(address):
            maybeCallAfter(self._newHub, address)
            return HubEventListManager(self, address, None, addBaseTreeItem)

        error_push = EndpointEventListManager(None, None, None,
            addBaseTreeItem, event_list=self.error_list).push

        def newPipe(address, endpoint):
            maybeCallAfter(self._newEndpoint, address, endpoint)
            result = EndpointEventListManager(self, address, endpoint,
                addBaseTreeItem)
            if endpoint == 0:
                result = Endpoint0TransferAggregator(result, error_push)
            return result

        update_delta = self.load_gauge.GetRange() / 100
        read_length = last_update = 0
        stream = ReorderedStream(
            Packetiser(
                TransactionAggregator(
                    PipeAggregator(
                        busEvent,
                        error_push,
                        newHub,
                        newPipe,
                    ),
                    error_push,
                ),
                captureEvent,
            )
        )
        parse = stream.push
        if use_gauge:
            gauge = self.load_gauge
            SetGaugeValue = gauge.SetValue
        while True:
            data = read(read_buf)
            if not data:
                break
            if use_gauge:
                read_length += len(data)
                if read_length > last_update + update_delta:
                    sys.stdout.write('.')
                    sys.stdout.flush()
                    last_update = read_length
                    # Side effect: causes a gui refresh, flushing "CallAfter" queue.
                    wx.MutexGuiEnter()
                    try:
                        SetGaugeValue(read_length)
                    finally:
                        wx.MutexGuiLeave()
            try:
                parse(data)
            except ParsingDone:
                break
        maybeCallAfter(flushTreeList)
        stream.stop()
        if use_gauge:
            maybeCallAfter(gauge.Show, False)

def main():
    if len(sys.argv) == 2:
        loadfile = sys.argv[1]
    else:
        loadfile = None
    app = wx.PySimpleApp(0)
    wx.InitAllImageHandlers()
    main_frame = ITI1480AMainFrame(None, -1, "", loadfile=loadfile)
    app.SetTopWindow(main_frame)
    main_frame.Show()
    app.MainLoop()

if __name__ == '__main__':
    main()

