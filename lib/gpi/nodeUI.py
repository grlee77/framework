#    Copyright (C) 2014  Dignity Health
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Lesser General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Lesser General Public License for more details.
#
#    You should have received a copy of the GNU Lesser General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#    NO CLINICAL USE.  THE SOFTWARE IS NOT INTENDED FOR COMMERCIAL PURPOSES
#    AND SHOULD BE USED ONLY FOR NON-COMMERCIAL RESEARCH PURPOSES.  THE
#    SOFTWARE MAY NOT IN ANY EVENT BE USED FOR ANY CLINICAL OR DIAGNOSTIC
#    PURPOSES.  YOU ACKNOWLEDGE AND AGREE THAT THE SOFTWARE IS NOT INTENDED FOR
#    USE IN ANY HIGH RISK OR STRICT LIABILITY ACTIVITY, INCLUDING BUT NOT
#    LIMITED TO LIFE SUPPORT OR EMERGENCY MEDICAL OPERATIONS OR USES.  LICENSOR
#    MAKES NO WARRANTY AND HAS NOR LIABILITY ARISING FROM ANY USE OF THE
#    SOFTWARE IN ANY HIGH RISK OR STRICT LIABILITY ACTIVITIES.


import os
import imp
import time
import copy
import hashlib
import inspect
import traceback
import collections
from multiprocessing import sharedctypes # numpy xfer

# gpi
import gpi
from gpi import QtCore, QtGui
from .defines import ExternalNodeType, GPI_PROCESS, GPI_THREAD, stw, GPI_SHDM_PATH
from .defines import GPI_WIDGET_EVENT, REQUIRED, OPTIONAL, GPI_PORT_EVENT
from .dataproxy import DataProxy, ProxyType
from .logger import manager
from .port import InPort, OutPort
from .widgets import HidableGroupBox
from . import widgets as BUILTIN_WIDGETS
from . import syntax


# start logger for this module
log = manager.getLogger(__name__)

# for PROCESS data hack
import numpy as np

# Developer Interface Exceptions
# TODO: new exceptions (GPIError_nodeUI_*) here?
# class GPIError_nodeAPI_setData(Exception):
#     def __init__(self, value):
#         super(GPIError_nodeAPI_setData, self).__init__(value)
#
# class GPIError_nodeAPI_getData(Exception):
#     def __init__(self, value):
#         super(GPIError_nodeAPI_getData, self).__init__(value)
#
# class GPIError_nodeAPI_setAttr(Exception):
#     def __init__(self, value):
#         super(GPIError_nodeAPI_setAttr, self).__init__(value)
#
# class GPIError_nodeAPI_getAttr(Exception):
#     def __init__(self, value):
#         super(GPIError_nodeAPI_getAttr, self).__init__(value)
#
# class GPIError_nodeAPI_getVal(Exception):
#     def __init__(self, value):
#         super(GPIError_nodeAPI_getVal, self).__init__(value)


class NodeUI(QtGui.QWidget):
    '''This is the class that all external modules must implement.'''
    NodeUIType = ExternalNodeType  # ensures the subclass is of THIS class
    modifyWdg = gpi.Signal(str, dict)

    def __init__(self, node):
        super().__init__()

        #self.setToolTip("Double Click to Show/Hide each Widget")
        self.node = node

        self._name = node.getModuleName()
        self._label = ''
        self._detailLabel = ''
        self._docText = None
        # self.parmList = []  # deprecated, since dicts have direct name lookup
        self._parmDict = collections.OrderedDict() # mirror parmList for now
        self.parmSettings = {}  # for buffering wdg parms before copying to a PROCESS
        self.shdmDict = {} # for storing base addresses

        # this must exist before user-widgets are added so that they can get
        # node label updates
        self.wdglabel = QtGui.QLineEdit(self._label)

        # allow logger to be used in initUI()
        self.log = manager.getLogger(self._name)

        # grid for module widgets
        self.layout = QtGui.QGridLayout()

        # instantiate the layout
        self.setLayout(self.layout)

        self.setTitle(node.getModuleName())

        self._starttime = 0
        self._startline = 0

    def initUI(self, widgets, inPorts, outPorts, nodeCatItem=None):
        """Create the actual widgets based on the description from NodeAPI"""
        # self._widgets = widgets
        for title, params in widgets.items():
            wdgGroup = nodeCatItem.getWidget(params['wdg'])
            self.addWidget(title=title, wdgGroup=wdgGroup, **params)

        # run through all widget titles since each widget parent is now set.
        [parm.setDispTitle() for parm in self.getParmList()]

        for title, params in inPorts.items():
            self.addInPort(title=title, **params)

        for title, params in outPorts.items():
            self.addOutPort(title=title, **params)

        # make a label box with the unique id
        labelGroup = HidableGroupBox("Node Label")
        labelLayout = QtGui.QGridLayout()
        self.wdglabel.textChanged.connect(self.setLabel)
        labelLayout.addWidget(self.wdglabel, 0, 1)
        labelGroup.setLayout(labelLayout)
        labelGroup.set_collapsed(True)
        labelGroup.setToolTip("Displays the Label on the Canvas (Double Click)")
        self.layout.addWidget(labelGroup, len(self._parmDict) + 1, 0)

        # make an about box with the unique id
        aboutGroup = HidableGroupBox("About")
        aboutLayout = QtGui.QGridLayout()
        about_button = QtGui.QPushButton("Open Node &Documentation")
        about_button.clicked.connect(self.openNodeDocumentation)
        aboutLayout.addWidget(about_button, 0, 1)
        aboutGroup.setLayout(aboutLayout)
        aboutGroup.set_collapsed(True)
        aboutGroup.setToolTip("Node Documentation (docstring + autodocs, Double Click)")
        self.layout.addWidget(aboutGroup, len(self._parmDict) + 2, 0)

        # window (just a QTextEdit) that will show documentation text
        self.doc_text_win = QtGui.QTextEdit()
        self.doc_text_win.setPlainText(self.generateHelpText())
        self.doc_text_win.setReadOnly(True)
        doc_text_font = QtGui.QFont("Monospace", 14)
        self.doc_text_win.setFont(doc_text_font)
        self.doc_text_win.setLineWrapMode(QtGui.QTextEdit.NoWrap)
        self.doc_text_win.setWindowTitle(self._name + " Documentation")

        # make widget menus scrollable
        self._scrollArea = QtGui.QScrollArea()
        self._scrollArea.setWidget(self)
        self._scrollArea.setWidgetResizable(True)
        self._scroll_grip = QtGui.QSizeGrip(self)
        self._scrollArea.setCornerWidget(self._scroll_grip)
        self._scrollArea.setGeometry(50, 50, 1000, 2000)

        hbox = QtGui.QHBoxLayout()
        self._statusbar_sys = QtGui.QLabel('')
        self._statusbar_usr = QtGui.QLabel('')
        hbox.addWidget(self._statusbar_sys)
        hbox.addWidget(self._statusbar_usr, 0, (QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter))

        # window resize grip
        self._grip = QtGui.QSizeGrip(self)
        hbox.addWidget(self._grip)

        self.layout.addLayout(hbox, len(self._parmDict) + 3, 0)
        self.layout.setRowStretch(len(self._parmDict) + 3, 0)

    def getWidgets(self):
        # return a list of widgets
        widgets = {}
        for widget in self.getParmList():
            widgets[widget.title()] = {'wdg' : widget.__class__.__name__}
            widgets[widget.title()].update(**widget.getSettings()['kwargs'])
        return widgets

    def getWidgetNames(self):
        return list(self._parmDict.keys())

    def getParmList(self):
        return list(self._parmDict.values())

    def starttime(self):
        self._startline = inspect.currentframe().f_back.f_lineno
        self._starttime = time.time()

    def endtime(self, msg=''):
        ttime = time.time() - self._starttime
        eline = inspect.currentframe().f_back.f_lineno
        log.node(self.node.getName()+' - '+str(ttime)+'sec, between lines:'+str(self._startline)+'-'+str(eline)+'. '+msg)

    def stringifyExecType(self):
        if self.execType() is GPI_PROCESS:
            return " [Process]"
        elif self.execType() is GPI_THREAD:
            return " [Thread]"
        else:
            return " [App-Loop]"

    def setStatus_sys(self, msg):
        msg += self.stringifyExecType()
        self._statusbar_sys.setText(msg)

    def setStatus(self, msg):
        self._statusbar_usr.setText(msg)

    def execType(self):
        # default executable type
        # return GPI_THREAD
        return GPI_PROCESS  # this is the safest
        # return GPI_APPLOOP

    def getLabel(self):
        return self._label

    def moduleExists(self, name):
        '''Give the user a simple module checker for the node validate
        function.
        '''
        try:
            imp.find_module(name)
        except ImportError:
            return False
        else:
            return True

    def moduleValidated(self, name):
        '''Provide a stock error message in the event a c/ext module cannot
        be found.
        '''
        if not self.moduleExists(name):
            log.error("The \'" + name + "\' module cannot be found, compute() aborted.")
            return 1
        return 0

    def setLabelWidget(self, newlabel=''):
        self.wdglabel.setText(newlabel)

    def setLabel(self, newlabel=''):
        self._label = str(newlabel)
        self.updateTitle()
        self.node.updateOutportPosition()
        self.node.graph.scene().update(self.node.boundingRect())
        self.node.update()

    def openNodeDocumentation(self):
        self.doc_text_win.show()

        # setting the size only works if we have shown the widget
        # set the width based on some ideal width (max at 800px)
        docwidth = self.doc_text_win.document().idealWidth()
        lmargin = self.doc_text_win.contentsMargins().left()
        rmargin = self.doc_text_win.contentsMargins().right()
        scrollbar_width = 20 # estimate, scrollbar overlaps content otherwise
        total_width = min(lmargin + docwidth + rmargin + scrollbar_width, 800)
        self.doc_text_win.setFixedWidth(total_width)

        # set the height based on the content size
        docheight= self.doc_text_win.document().size().height()
        self.doc_text_win.setMinimumHeight(min(docheight, 200))
        self.doc_text_win.setMaximumHeight(docheight)

    def setDetailLabel(self, newDetailLabel='', elideMode='middle'):
        '''An additional label displayed on the node directly'''
        self._detailLabel = str(newDetailLabel)
        self._detailElideMode = elideMode
        self.node.updateOutportPosition()

    def getDetailLabel(self):
        '''An additional label displayed on the node directly'''
        return self._detailLabel

    def getDetailLabelElideMode(self):
        '''How the detail label should be elided if it's too long:'''
        mode = self._detailElideMode
        qt_mode = QtCore.Qt.ElideMiddle
        if mode == 'left':
            qt_mode = QtCore.Qt.ElideLeft
        elif mode == 'right':
            qt_mode = QtCore.Qt.ElideRight
        elif mode == 'none':
            qt_mode = QtCore.Qt.ElideNone
        else: # default, mode == 'middle'
            qt_mode = QtCore.Qt.ElideMiddle

        return qt_mode

    def windowActivationChange(self, test):
        self.node.graph.scene().makeOnlyTheseNodesSelected([self.node])

    def getSettings(self):  # NODEAPI
        '''Wrap up all settings from each widget.'''
        s = {}
        s['label'] = self._label
        s['parms'] = []
        for parm in self.getParmList():
            s['parms'].append(copy.deepcopy(parm.getSettings()))
        return s

    def loadSettings(self, s):
        self.setLabelWidget(s['label'])

        # modify node widgets
        for parm in s['parms']:
            # the NodeAPI has instantiated the widget by name, this will
            # change the wdg-ID, however, this step is only dependend on
            # unique widget names.

            if not self.getWidget(parm['name']):
                log.warn("Trying to load settings; can't find widget with name: \'" + \
                    stw(parm['name']) + "\', skipping...")
                continue

            log.debug('Setting widget: \'' + stw(parm['name']) + '\'')
            try:
                self.modifyWidget_direct(parm['name'], **parm['kwargs'])
            except:
                log.error('Failed to set widget: \'' + stw(parm['name']) + '\'\n' + str(traceback.format_exc()))

            if parm['kwargs']['inport']:  # widget-inports
                self.addWidgetInPortByName(parm['name'])
            if parm['kwargs']['outport']:  # widget-outports
                self.addWidgetOutPortByName(parm['name'])


    def generateHelpText(self):
        """Gather the __doc__ string of the ExternalNode derived class,
        all the set_ methods for each attached widget, and any attached
        GPI-types from each of the ports.
        """
        self._docText = 'TODO: FIX THIS'
        if self._docText is not None:
            return self._docText

        # NODE DOC
        node_doc = "NODE: \'" + self.node._moduleName + "\'\n" + "    " + \
            str(self.__doc__)

        # WIDGETS DOC
        # parm_doc = ""  # contains parameter info
        wdg_doc = "\n\nAPPENDIX A: (WIDGETS)\n"  # generic widget ref info
        for parm in self.getParmList():  # wdg order matters
            wdg_doc += "\n  \'" + parm.getTitle() + "\': " + \
                str(parm.__class__) + "\n"
            numSpaces = 8
            set_doc = "\n".join((numSpaces * " ") + i for i in str(
                parm.__doc__).splitlines())
            wdg_doc += set_doc + "\n"
            # set methods
            for member in dir(parm):
                if member.startswith('set_'):
                    wdg_doc += (8 * " ") + member + inspect.formatargspec(
                        *inspect.getargspec(getattr(parm, member)))
                    set_doc = str(inspect.getdoc(getattr(parm, member)))
                    numSpaces = 16
                    set_doc = "\n".join((
                        numSpaces * " ") + i for i in set_doc.splitlines())
                    wdg_doc += "\n" + set_doc + "\n"

        # PORTS DOC
        port_doc = "\n\nAPPENDIX B: (PORTS)\n"  # get the port type info
        for port in self.node.getPorts():
            typ = port.GPIType()
            port_doc += "\n  \'" + port.portTitle + "\': " + \
                str(typ.__class__) + "|" + str(type(port)) + "\n"
            numSpaces = 8
            set_doc = "\n".join((numSpaces * " ") + i for i in str(
                typ.__doc__).splitlines())
            port_doc += set_doc + "\n"

            # set methods
            for member in dir(typ):
                if member.startswith('set_'):
                    port_doc += (8 * " ") + member + inspect.formatargspec(
                        *inspect.getargspec(getattr(typ, member)))
                    set_doc = str(inspect.getdoc(getattr(typ, member)))
                    numSpaces = 16
                    set_doc = "\n".join((
                        numSpaces * " ") + i for i in set_doc.splitlines())
                    port_doc += "\n" + set_doc + "\n"

        # GETTERS/SETTERS
        getset_doc = "\n\nAPPENDIX C: (GETTERS/SETTERS)\n\n"
        getset_doc += (8 * " ") + "Node Initialization Setters: (initUI())\n\n"
        getset_doc += self.formatFuncDoc(self.addWidget)
        getset_doc += self.formatFuncDoc(self.addInPort)
        getset_doc += self.formatFuncDoc(self.addOutPort)
        getset_doc += (
            8 * " ") + "Node Compute Getters/Setters: (compute())\n\n"
        getset_doc += self.formatFuncDoc(self.getVal)
        getset_doc += self.formatFuncDoc(self.getAttr)
        getset_doc += self.formatFuncDoc(self.setAttr)
        getset_doc += self.formatFuncDoc(self.getData)
        getset_doc += self.formatFuncDoc(self.setData)

        self._docText = node_doc  # + wdg_doc + port_doc + getset_doc

        self.doc_text_win.setPlainText(self._docText)

        return self._docText

    def formatFuncDoc(self, func):
        """Generate auto-doc for passed func obj."""
        numSpaces = 24
        fdoc = inspect.getdoc(func)
        set_doc = "\n".join((
            numSpaces * " ") + i for i in str(fdoc).splitlines())
        rdoc = (16 * " ") + func.__name__ + \
            inspect.formatargspec(*inspect.getargspec(func)) \
            + "\n" + set_doc + "\n\n"
        return rdoc

    def printWidgetValues(self):
        # for debugging
        for parm in self.getParmList():
            log.debug("widget: " + str(parm))
            log.debug("value: " + str(parm.get_val()))

    # abstracting IF for user
    def addInPort(self, title=None, type=None, obligation=REQUIRED,
                  menuWidget=None, cyclic=False, **kwargs):
        """title = (str) port-title shown in tooltips
        type = (str) class name of extended type
        obligation = gpi.REQUIRED or gpi.OPTIONAL (default REQUIRED)
        menuWidget = INTERNAL USE
        kwargs = any set_<arg> method belonging to the
                    GPIDefaultType derived class.
        """
        self.node.addInPort(title, type, obligation, menuWidget, cyclic, **kwargs)
        self.node.update()

    # abstracting IF for user
    def addOutPort(self, title=None, type=None, obligation=REQUIRED,
                   menuWidget=None, **kwargs):
        """title = (str) port-title shown in tooltips
        type = (str) class name of extended type
        obligation = dummy parm to match function footprint
        menuWidget = INTERNAL USE
        kwargs = any set_<arg> method belonging to the
                    GPIDefaultType derived class.
        """
        self.node.addOutPort(title, type, obligation, menuWidget, **kwargs)
        self.node.update()

    def addWidgetInPortByName(self, title):
        wdg = self.findWidgetByName(title)
        self.addWidgetInPort(wdg)

    def addWidgetInPortByID(self, wdgID):
        wdg = self.findWidgetByID(wdgID)
        self.addWidgetInPort(wdg)

    def addWidgetInPort(self, wdg):
        self.addInPort(title=wdg.getTitle(), type=wdg.getDataType(),
                       obligation=OPTIONAL, menuWidget=wdg)

    def removeWidgetInPort(self, wdg):
        port = self.findWidgetInPortByName(wdg.getTitle())
        self.node.removePortByRef(port)

    def addWidgetOutPortByID(self, wdgID):
        wdg = self.findWidgetByID(wdgID)
        self.addWidgetOutPort(wdg)

    def addWidgetOutPortByName(self, title):
        wdg = self.findWidgetByName(title)
        self.addWidgetOutPort(wdg)

    def addWidgetOutPort(self, wdg):
        self.addOutPort(
            title=wdg.getTitle(), type=wdg.getDataType(), menuWidget=wdg)

    def removeWidgetOutPort(self, wdg):
        port = self.findWidgetOutPortByName(wdg.getTitle())
        self.node.removePortByRef(port)

    def addWidget(self, wdg=None, title=None, wdgGroup=None, **kwargs):
        """wdg = (str) corresponds to the widget class name
        title = (str) is the string label given in the node-menu
        kwargs = corresponds to the set_<arg> methods specific
                    to the chosen wdg-class.
        """
        if (wdg is None) or (title is None):
            log.critical("addWidget(): widgets need a title" \
                + " AND a wdg-str! Aborting.")
            return

        # check existence first
        if self.node.titleExists(title):
            log.critical("addWidget(): Widget title \'" + str(title) \
                + "\' is already in use! Aborting.")
            return

        ypos = len(self._parmDict)

        # get widget from standard gpi widgets
        if wdgGroup is None:
            if hasattr(BUILTIN_WIDGETS, wdg):
                wdgGroup = getattr(BUILTIN_WIDGETS, wdg)

        # if its still not found then its a big deal
        if wdgGroup is None:
            log.critical("\'" + wdg + "\' widget not found.")
            raise Exception("Widget not found.")

        # instantiate if not None
        else:
            wdgGroup = wdgGroup(title, parent=self)

        wdgGroup.setNodeName(self.node.getModuleName())
        wdgGroup._setNodeLabel(self._label)

        wdgGroup.valueChanged.connect(lambda: self.wdgEvent(title))
        wdgGroup.portStateChange.connect(lambda: self.changePortStatus(title))
        wdgGroup.returnWidgetToOrigin.connect(self.returnWidgetToNodeMenu)
        self.wdglabel.textChanged.connect(wdgGroup._setNodeLabel)

        # add to menu layout
        self.layout.addWidget(wdgGroup, ypos, 0)
        self._parmDict[title] = wdgGroup  # the new way

        self.modifyWidget_direct(title, **kwargs)

    def returnWidgetToNodeMenu(self, wdgid):
        # find widget by id
        wdgid = int(wdgid)
        ind = 0
        for parm in self.getParmList():
            if wdgid == parm.get_id():
                self.layout.addWidget(parm, ind, 0)
                if self.node.isMarkedForDeletion():
                    parm.hide()
                else:
                    parm.show()
                break
            ind += 1

    def blockWdgSignals(self, val):
        '''Block all signals, especially valueChanged.'''
        for parm in self.getParmList():
            parm.blockSignals(val)

    def blockSignals_byWdg(self, title, val):
        # get the over-reporting widget by name and block it
        self._parmDict[title].blockSignals(val)

    def changePortStatus(self, title):
        '''Add a new in- or out-port tied to this widget.'''
        log.debug("changePortStatus: " + str(title))
        wdg = self.findWidgetByName(title)

        # make sure the port is either added or will be added.
        if wdg.inPort_ON:
            if self.findWidgetInPortByName(title) is None:
                self.addWidgetInPort(wdg)
        else:
            if self.findWidgetInPortByName(title):
                self.removeWidgetInPort(wdg)
        if wdg.outPort_ON:
            if self.findWidgetOutPortByName(title) is None:
                self.addWidgetOutPort(wdg)
        else:
            if self.findWidgetOutPortByName(title):
                self.removeWidgetOutPort(wdg)

    def findWidgetByName(self, title):
        for parm in self.getParmList():
            if parm.getTitle() == title:
                return parm

    def findWidgetByID(self, wdgID):
        for parm in self.getParmList():
            if parm.id() == wdgID:
                return parm

    def findWidgetInPortByName(self, title):
        for port in self.node.inportList:
            if port.isWidgetPort():
                if port.portTitle == title:
                    return port

    def findWidgetOutPortByName(self, title):
        for port in self.node.outportList:
            if port.isWidgetPort():
                if port.portTitle == title:
                    return port

    def modifyWidget_setter(self, src, kw, val):
        sfunc = "set_" + kw
        if hasattr(src, sfunc):
            func = getattr(src, sfunc)
            func(val)
        else:
            try:
                ttl = src.getTitle()
            except:
                ttl = str(src)

            log.critical("modifyWidget_setter(): Widget \'" + stw(ttl) \
                + "\' doesn't have attr \'" + stw(sfunc) + "\'.")

    def modifyWidget_direct(self, pnumORtitle, **kwargs):
        src = self.getWidget(pnumORtitle)

        for k, v in kwargs.items():
            if k != 'val':
                self.modifyWidget_setter(src, k, v)

        # set 'val' last so that bounds don't cause a temporary conflict.
        if 'val' in kwargs:
            self.modifyWidget_setter(src, 'val', kwargs['val'])

    def modifyWidget_buffer(self, title, **kwargs):
        '''GPI_PROCESSes have to use buffered widget attributes to effect the
        same changes to attributes during compute() as with GPI_THREAD or
        GPI_APPLOOP.
        '''
        src = self.getWdgFromBuffer(title)

        try:
            for k, v in list(kwargs.items()):
                src['kwargs'][k] = v
        except:
            log.critical("modifyWidget_buffer() FAILED to modify buffered attribute")


    def lockWidgetUpdates(self):
        self._widgetUpdateMutex_locked = True

    def unlockWidgetUpdates(self):
        self._widgetUpdateMutex_locked = False

    def widgetsUpdatesLocked(self):
        return self._widgetUpdateMutex_locked

    def wdgEvent(self, title):
        # Captures all valueChanged events from widgets.
        # 'title' is for interrogating who changed in compute().

        # Once the event status has been set, disable valueChanged signals
        # until the node has successfully completed.
        # -this was b/c sliders etc. were causing too many signals resulting
        # in a recursion overload to this function.
        #self.blockWdgSignals(True)
        self.blockSignals_byWdg(title, True)

        if self.node.hasEventPending():
            self.node.appendEvent({GPI_WIDGET_EVENT: title})
            return
        else:
            self.node.setEventStatus({GPI_WIDGET_EVENT: title})

        # Can start event from any applicable 'check' transition
        if self.node.graph.inIdleState():
            self.node.graph._switchSig.emit('check')

    # for re-drawing the menu title to match module/node instance
    def updateTitle(self):
        # Why is this trying the scrollArea? isn't it always a scroll???
        if self._label == '':
            try:
                self._scrollArea.setWindowTitle(self.node.name)
            except:
                self.setWindowTitle(self.node.name)
        else:
            try:
                augtitle = self.node.name + ": " + self._label
                self._scrollArea.setWindowTitle(augtitle)
            except:
                augtitle = self.node.name + ": " + self._label
                self.setWindowTitle(augtitle)

    def setTitle(self, title):
        self.node.name = title
        self.updateTitle()

    # Queue actions for widgets and ports
    def setAttr(self, title, **kwargs):
        """title = (str) the corresponding widget name.
        kwargs = args corresponding to the get_<arg> methods of the wdg-class.
        """
        try:
            # start = time.time()
            # either emit a signal or save a queue
            if self.node.inDisabledState():
                return
            if self.node.nodeCompute_thread.execType() == GPI_PROCESS:
                # PROCESS
                self.node.nodeCompute_thread.addToQueue(['modifyWdg', title, kwargs])
                #self.modifyWidget_buffer(title, **kwargs)
            elif self.node.nodeCompute_thread.execType() == GPI_THREAD:
                # THREAD
                # can't modify QObjects in thread, but we can send a signal to do
                # so
                self.modifyWdg.emit(title, kwargs)
            else:
                # APPLOOP
                self.modifyWidget_direct(str(title), **kwargs)

        except:
            raise GPIError_nodeAPI_setAttr('self.setAttr(\''+stw(title)+'\',...) failed in the node definition, check the widget name, attribute name and attribute type().')

        # log.debug("modifyWdg(): time: "+str(time.time() - start)+" sec")

    def allocArray(self, shape=(1,), dtype=np.float32, name='local'):
        '''return a shared memory array if the node is run as a process.
            -the array name needs to be unique
        '''
        if self.node.nodeCompute_thread.execType() == GPI_PROCESS:
            buf, shd = DataProxy()._genNDArrayMemmap(shape, dtype, self.node.getID(), name)

            if shd is not None:
                # saving the reference id allows the node developer to decide
                # on the fly if the preallocated array will be used in the final
                # setData() call.
                self.shdmDict[str(id(buf))] = shd.filename

            return buf
        else:
            return np.ndarray(shape, dtype=dtype)

    def setData(self, title, data):
        """title = (str) name of the OutPort to send the object reference.
        data = (object) any object corresponding to a GPIType class.
        """
        try:
            # start = time.time()
            # either set directly or save a queue
            if self.node.inDisabledState():
                return
            if self.node.nodeCompute_thread.execType() == GPI_PROCESS:

                #  numpy arrays
                if type(data) is np.memmap or type(data) is np.ndarray:
                    if str(id(data)) in self.shdmDict: # pre-alloc
                        s = DataProxy().NDArray(data, shdf=self.shdmDict[str(id(data))], nodeID=self.node.getID(), portname=title)
                    else:
                        s = DataProxy().NDArray(data, nodeID=self.node.getID(), portname=title)

                    # for split objects to pass thru individually
                    # this will be a list of DataProxy objects
                    if type(s) is list:
                        for i in s:
                            self.node.nodeCompute_thread.addToQueue(['setData', title, i])
                    # a single DataProxy object
                    else:
                        self.node.nodeCompute_thread.addToQueue(['setData', title, s])

                # all other non-numpy data that are pickleable
                else:
                    # PROCESS output other than numpy
                    self.node.nodeCompute_thread.addToQueue(['setData', title, data])
            else:
                # THREAD or APPLOOP
                self.node.setData(title, data)
                # log.debug("setData(): time: "+str(time.time() - start)+" sec")

        except:
            print((str(traceback.format_exc())))
            raise GPIError_nodeAPI_setData('self.setData(\''+stw(title)+'\',...) failed in the node definition, check the output name and data type().')

    def getData(self, title):
        """title = (str) the name of the InPort.
        """
        try:
            port = self.node.getPortByNumOrTitle(title)
            if isinstance(port, InPort):
                data = port.getUpstreamData()
                if type(data) is np.ndarray:
                    # don't allow users to change original array attributes
                    # that aren't protected by the 'writeable' flag
                    buf = np.frombuffer(data.data, dtype=data.dtype)
                    buf.shape = tuple(data.shape)
                    return buf
                else:
                    return data

            elif isinstance(port, OutPort):
                return port.data
            else:
                raise Exception("getData", "Invalid Port Title")
        except:
            raise GPIError_nodeAPI_getData('self.getData(\''+stw(title)+'\') failed in the node definition check the port name.')

    def getPortData(self):
        port_data = {}
        for port in self.node.inportList:
            port_data[port.portTitle] = {'data' : port.getUpstreamData()}
        for port in self.node.outportList:
            port_data[port.portTitle] = {'data' : port._data}
        return port_data

    # TODO: deprecate these?
    def getInPort(self, pnumORtitle):
        return self.node.getInPort(pnumORtitle)

    def getOutPort(self, pnumORtitle):
        return self.node.getOutPort(pnumORtitle)

    def getWidgetByID(self, wdgID):
        for parm in self.getParmList():
            if parm.id() == wdgID:
                return parm
        log.critical("getWidgetByID(): Cannot find widget id:" + str(wdgID))

    def getWidget(self, pnum):
        '''Returns the widget desc handle and position number'''
        # fetch by widget number
        if type(pnum) is int:
            if (pnum < 0) or (pnum >= len(self._parmDict)):
                log.error("getWidget(): Target widget out of range: " + str(pnum))
                return
            src = self.getParmList()[pnum]
        # fetch by widget title
        elif type(pnum) is str:
            try:
                src = self._parmDict[pnum]
            except KeyError:
                log.error("getWidget(): Failed to find widget: \'" + stw(pnum) + "\'")
                return
        else:
            log.error("getWidget(): Widget identifier must be" + " either int or str")
            return
        return src

    def bufferParmSettings(self):
        '''Get list of parms (dict) in self.parmSettings['parms'].
        Called by GPI_PROCESS functor to capture all widget settings needed
        in compute().
        '''
        self.parmSettings = self.getSettings()

    def getWdgFromBuffer(self, title):
        # find a specific widget by title
        for wdg in self.parmSettings['parms']:
            if 'name' in wdg:
                if wdg['name'] == title:
                    return wdg

    def getVal(self, title):
        """Returns get_val() from wdg-class (see getAttr()).
        """
        try:
            # Fetch widget value by title
            if self.node.inDisabledState():
                return

            if self.node.nodeCompute_thread.execType() == GPI_PROCESS:
                return self.getAttr(title, 'val')

            # threads and main loop can access directly
            return self.getAttr(title, 'val')

        except:
            print(str(traceback.format_exc()))
            raise GPIError_nodeAPI_getVal('self.getVal(\''+stw(title)+'\') failed in the node definition, check the widget name.')

    def getAttr(self, title, attr):
        """title = (str) wdg-class name
        attr = (str) corresponds to the get_<arg> of the desired attribute.
        """
        try:
            # Fetch widget value by title
            if self.node.inDisabledState():
                return

            if self.node.nodeCompute_thread.execType() == GPI_PROCESS:
                wdg = self.getWdgFromBuffer(title)
                return self._getAttr_fromWdg(wdg, attr)

            # threads and main loop can access directly
            wdg = self.getWidget(title)
            return self._getAttr_fromWdg(wdg, attr)

        except:
            print(str(traceback.format_exc()))
            raise GPIError_nodeAPI_getAttr('self.getAttr(\''+stw(title)+'\',...) failed in the node definition, check widget name and attribute name.')

    def _getAttr_fromWdg(self, wdg, attr):
        try:
            # input is either a dict or a wdg instance.
            if isinstance(wdg, dict):  # buffered values
                if attr in wdg['kwargs']:
                    return wdg['kwargs'][attr]
                else:
                    # build error msg
                    title = wdg['name']
                    funame = attr
            else:  # direct widget access
                funame = 'get_' + attr
                if hasattr(wdg, funame):
                    return getattr(wdg, funame)()
                else:
                    try:
                        title = wdg.getTitle()
                    except:
                        title = "\'no name\'"
            log.critical("_getAttr(): widget \'" + stw(title) + "\' has no attr \'" + stw(funame) + "\'.")
            #return None
            raise GPIError_nodeAPI_getAttr('_getAttr() failed for widget \''+stw(title)+'\'')
        except:
            #log.critical("_getAttr_fromWdg(): Likely the wrong input arg type.")
            #raise
            print(str(traceback.format_exc()))
            raise GPIError_nodeAPI_getAttr('_getAttr() failed for widget \''+stw(title)+'\'')

    def post_compute_widget_update(self):
        # reset any widget that requires it (i.e. PushButton)
        for parm in self.getParmList():
            if hasattr(parm, 'set_reset'):
                parm.set_reset()