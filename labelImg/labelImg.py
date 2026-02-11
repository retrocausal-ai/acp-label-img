#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import codecs
import distutils.spawn
import os.path
import platform
import re
import sys
import subprocess
import shutil
import webbrowser as wb

from functools import partial
from collections import defaultdict

try:
    from PyQt5.QtGui import *
    from PyQt5.QtCore import *
    from PyQt5.QtWidgets import *
except ImportError:
    # needed for py3+qt4
    # Ref:
    # http://pyqt.sourceforge.net/Docs/PyQt4/incompatible_apis.html
    # http://stackoverflow.com/questions/21217399/pyqt4-qtcore-qvariant-object-instead-of-a-string
    if sys.version_info.major >= 3:
        import sip
        sip.setapi('QVariant', 2)
    from PyQt4.QtGui import *
    from PyQt4.QtCore import *

from libs.combobox import ComboBox
from libs.resources import *
from libs.constants import *
from libs.utils import *
from libs.settings import Settings
from libs.shape import Shape, DEFAULT_LINE_COLOR, DEFAULT_FILL_COLOR
from libs.stringBundle import StringBundle
from libs.canvas import Canvas
from libs.zoomWidget import ZoomWidget
from libs.labelDialog import LabelDialog
from libs.colorDialog import ColorDialog
from libs.labelFile import LabelFile, LabelFileError, LabelFileFormat
from libs.toolBar import ToolBar
from libs.pascal_voc_io import PascalVocReader
from libs.pascal_voc_io import XML_EXT
from libs.yolo_io import YoloReader
from libs.yolo_io import TXT_EXT
from libs.create_ml_io import CreateMLReader
from libs.create_ml_io import JSON_EXT
from libs.ustr import ustr
from libs.hashableQListWidgetItem import HashableQListWidgetItem

__appname__ = 'labelImg'


class WindowMixin(object):

    def menu(self, title, actions=None):
        menu = self.menuBar().addMenu(title)
        if actions:
            add_actions(menu, actions)
        return menu

    def toolbar(self, title, actions=None):
        toolbar = ToolBar(title)
        toolbar.setObjectName(u'%sToolBar' % title)
        # toolbar.setOrientation(Qt.Vertical)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        toolbar.setIconSize(QSize(24, 24))  # Smaller icon size for compact layout
        toolbar.setContentsMargins(2, 2, 2, 2)  # Tight margins
        if actions:
            add_actions(toolbar, actions)
        self.addToolBar(Qt.LeftToolBarArea, toolbar)
        return toolbar


class MainWindow(QMainWindow, WindowMixin):
    FIT_WINDOW, FIT_WIDTH, MANUAL_ZOOM = list(range(3))

    def __init__(self, default_filename=None, default_prefdef_class_file=None, default_save_dir=None):
        super(MainWindow, self).__init__()
        self.setWindowTitle(__appname__)

        # Load setting in the main thread
        self.settings = Settings()
        self.settings.load()
        settings = self.settings

        self.os_name = platform.system()

        # Load string bundle for i18n
        self.string_bundle = StringBundle.get_bundle()
        get_str = lambda str_id: self.string_bundle.get_string(str_id)

        # Save as Pascal voc xml
        self.default_save_dir = default_save_dir
        self.label_file_format = settings.get(SETTING_LABEL_FILE_FORMAT, LabelFileFormat.YOLO)

        # For loading all image under a directory
        self.m_img_list = []
        self.dir_name = None
        self.label_hist = []
        self.last_open_dir = None
        self.cur_img_idx = 0
        self.img_count = 1

        # Whether we need to save or not.
        self.dirty = False

        self._no_selection_slot = False
        self._beginner = True
        self.screencast = "https://youtu.be/p0nR2YsCY_U"

        # Clipboard for copy/paste functionality
        self.clipboard = []
        self.clipboard_source_image = None  # Track which image the boxes were copied from

        # Color palette for per-class colors
        self.class_colors = {}  # Dictionary to store custom colors per class

        # Load predefined classes to the list
        self.load_predefined_classes(default_prefdef_class_file)

        # Main widgets and related state.
        self.label_dialog = LabelDialog(parent=self, list_item=self.label_hist)

        self.items_to_shapes = {}
        self.shapes_to_items = {}
        self.prev_label_text = ''

        list_layout = QVBoxLayout()
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(3)  # Reduced spacing between widgets

        # Create a widget for using default label
        self.use_default_label_checkbox = QCheckBox(get_str('useDefaultLabel'))
        self.use_default_label_checkbox.setChecked(False)
        self.default_label_combo = QComboBox()
        self.default_label_combo.setEditable(False)  # Dropdown is not editable, only selectable
        self.default_label_combo.setInsertPolicy(QComboBox.NoInsert)
        # Classes already added to combo in load_predefined_classes - no need to add again
        use_default_label_qhbox_layout = QHBoxLayout()
        use_default_label_qhbox_layout.setContentsMargins(0, 0, 0, 0)
        use_default_label_qhbox_layout.setSpacing(5)
        use_default_label_qhbox_layout.addWidget(self.use_default_label_checkbox)
        use_default_label_qhbox_layout.addWidget(self.default_label_combo)
        use_default_label_container = QWidget()
        use_default_label_container.setLayout(use_default_label_qhbox_layout)

        # Create a widget for edit and diffc button
        # Removed diffc_button and edit_button for cleaner UI

        # Add some of widgets to list_layout with stretch factor 0 (don't expand)
        list_layout.addWidget(use_default_label_container, 0)

        # Create a widget for bbox thickness control
        self.bbox_thickness_label = QLabel('BBox Thickness (px):')
        self.bbox_thickness_spinbox = QDoubleSpinBox()
        self.bbox_thickness_spinbox.setRange(0.5, 5.0)
        self.bbox_thickness_spinbox.setSingleStep(0.1)
        self.bbox_thickness_spinbox.setDecimals(1)
        self.bbox_thickness_spinbox.setValue(2.0)  # Default value
        self.bbox_thickness_spinbox.valueChanged.connect(self.bbox_thickness_changed)
        bbox_thickness_layout = QHBoxLayout()
        bbox_thickness_layout.setContentsMargins(0, 0, 0, 0)
        bbox_thickness_layout.setSpacing(5)
        bbox_thickness_layout.addWidget(self.bbox_thickness_label)
        bbox_thickness_layout.addWidget(self.bbox_thickness_spinbox)
        bbox_thickness_container = QWidget()
        bbox_thickness_container.setLayout(bbox_thickness_layout)
        list_layout.addWidget(bbox_thickness_container, 0)

        # Create NEW persistent class visibility filter with multi-select checkboxes
        self.class_visibility_label = QLabel('Class Visibility:')
        self.class_visibility_list = QListWidget()
        self.class_visibility_list.setMinimumHeight(220)  # Reduced to allow label list more space
        self.class_visibility_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.class_visibility_list.itemChanged.connect(self.class_visibility_changed)
        self.class_visibility_list.itemClicked.connect(self.class_visibility_item_clicked)

        # Add "Show All" option
        show_all_item = QListWidgetItem("Show All")
        show_all_item.setFlags(show_all_item.flags() | Qt.ItemIsUserCheckable)
        show_all_item.setCheckState(Qt.Checked)  # Default checked
        show_all_item.setData(Qt.UserRole, "")  # Empty string means show all
        self.class_visibility_list.addItem(show_all_item)

        class_visibility_layout = QVBoxLayout()
        class_visibility_layout.setContentsMargins(0, 5, 0, 0)
        class_visibility_layout.setSpacing(3)
        class_visibility_layout.addWidget(self.class_visibility_label)
        class_visibility_layout.addWidget(self.class_visibility_list)
        class_visibility_container = QWidget()
        class_visibility_container.setLayout(class_visibility_layout)
        list_layout.addWidget(class_visibility_container, 2)  # Stretch factor 2 - takes more space

        # Create and add a widget for showing current label items
        self.label_list = QListWidget()
        # Enable multi-selection with Ctrl+Click
        self.label_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # Increased size for better visibility
        self.label_list.setMinimumHeight(150)  # Increased from 100px
        self.label_list.setMaximumHeight(280)  # Increased from 200px
        self.label_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        label_list_container = QWidget()
        label_list_container.setLayout(list_layout)
        self.label_list.itemActivated.connect(self.label_selection_changed)
        self.label_list.itemSelectionChanged.connect(self.label_selection_changed)
        self.label_list.itemDoubleClicked.connect(self.edit_label)
        # Connect to itemChanged to detect checkbox changes.
        self.label_list.itemChanged.connect(self.label_item_changed)
        list_layout.addWidget(self.label_list, 1)  # Stretch factor 1 - gets some remaining space

        # Go to Image controls
        go_to_layout = QHBoxLayout()
        go_to_layout.setContentsMargins(0, 5, 0, 0)
        go_to_layout.setSpacing(5)

        self.go_to_image_input = QLineEdit()
        self.go_to_image_input.setPlaceholderText("Image #")
        self.go_to_image_input.setMaximumWidth(80)
        self.go_to_image_input.setFocusPolicy(Qt.ClickFocus)  # Only focus when clicked, not by tab
        self.go_to_image_input.returnPressed.connect(self.go_to_image)

        go_to_button = QPushButton("Go")
        go_to_button.setMaximumWidth(50)
        go_to_button.clicked.connect(self.go_to_image)

        go_to_layout.addWidget(self.go_to_image_input)
        go_to_layout.addWidget(go_to_button)
        go_to_layout.addStretch()

        list_layout.addLayout(go_to_layout)

        self.dock = QDockWidget(get_str('boxLabelText'), self)
        self.dock.setObjectName(get_str('labels'))
        self.dock.setWidget(label_list_container)

        self.file_list_widget = QListWidget()
        self.file_list_widget.itemDoubleClicked.connect(self.file_item_double_clicked)
        file_list_layout = QVBoxLayout()
        file_list_layout.setContentsMargins(0, 0, 0, 0)
        file_list_layout.addWidget(self.file_list_widget)
        file_list_container = QWidget()
        file_list_container.setLayout(file_list_layout)
        self.file_dock = QDockWidget(get_str('fileList'), self)
        self.file_dock.setObjectName(get_str('files'))
        self.file_dock.setWidget(file_list_container)

        self.zoom_widget = ZoomWidget()
        self.color_dialog = ColorDialog(parent=self)

        self.canvas = Canvas(parent=self)
        self.canvas.zoomRequest.connect(self.zoom_request)
        self.canvas.set_drawing_shape_to_square(settings.get(SETTING_DRAW_SQUARE, False))

        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setWidgetResizable(True)
        self.scroll_bars = {
            Qt.Vertical: scroll.verticalScrollBar(),
            Qt.Horizontal: scroll.horizontalScrollBar()
        }
        self.scroll_area = scroll
        self.canvas.scrollRequest.connect(self.scroll_request)

        self.canvas.newShape.connect(self.new_shape)
        self.canvas.shapeMoved.connect(self.set_dirty)
        self.canvas.selectionChanged.connect(self.shape_selection_changed)
        self.canvas.drawingPolygon.connect(self.toggle_drawing_sensitive)

        self.setCentralWidget(scroll)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.addDockWidget(Qt.RightDockWidgetArea, self.file_dock)
        self.file_dock.setFeatures(QDockWidget.DockWidgetFloatable)

        self.dock_features = QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetFloatable
        self.dock.setFeatures(self.dock.features() ^ self.dock_features)

        # Actions
        action = partial(new_action, self)
        quit = action(get_str('quit'), self.close,
                      None, 'quit', get_str('quitApp'))

        open = action(get_str('openFile'), self.open_file,
                      'Ctrl+O', 'open', get_str('openFileDetail'))

        open_dir = action(get_str('openDir'), self.open_dir_dialog,
                          'Ctrl+u', 'open', get_str('openDir'))

        change_save_dir = action(get_str('changeSaveDir'), self.change_save_dir_dialog,
                                 'Ctrl+r', 'open', get_str('changeSavedAnnotationDir'))

        open_annotation = action(get_str('openAnnotation'), self.open_annotation_dialog,
                                 'Ctrl+Shift+O', 'open', get_str('openAnnotationDetail'))
        copy_prev_bounding = action(get_str('copyPrevBounding'), self.copy_previous_bounding_boxes, 'Ctrl+Q', 'copy', get_str('copyPrevBounding'))

        open_next_image = action(get_str('nextImg'), self.open_next_image,
                                 'd', 'next', get_str('nextImgDetail'))

        open_prev_image = action(get_str('prevImg'), self.open_prev_image,
                                 'a', 'prev', get_str('prevImgDetail'))

        verify = action('Auto Annotate', self.auto_annotate_placeholder,
                        None, 'verify', 'Auto Annotate (placeholder)')

        save = action(get_str('save'), self.save_file,
                      'Ctrl+S', 'save', get_str('saveDetail'), enabled=False)

        def get_format_meta(format):
            """
            returns a tuple containing (title, icon_name) of the selected format
            """
            if format == LabelFileFormat.PASCAL_VOC:
                return '&PascalVOC', 'format_voc'
            elif format == LabelFileFormat.YOLO:
                return '&YOLO', 'format_yolo'
            elif format == LabelFileFormat.CREATE_ML:
                return '&CreateML', 'format_createml'

        save_format = action(get_format_meta(self.label_file_format)[0],
                             self.change_format, 'Ctrl+',
                             get_format_meta(self.label_file_format)[1],
                             get_str('changeSaveFormat'), enabled=True)

        save_as = action(get_str('saveAs'), self.save_file_as,
                         'Ctrl+Shift+S', 'save-as', get_str('saveAsDetail'), enabled=False)

        close = action(get_str('closeCur'), self.close_file, 'Ctrl+W', 'close', get_str('closeCurDetail'))

        delete_image = action(get_str('deleteImg'), self.delete_image, 'Ctrl+Shift+D', 'close', get_str('deleteImgDetail'))

        reset_all = action(get_str('resetAll'), self.reset_all, None, 'resetall', get_str('resetAllDetail'))

        color1 = action('Color Palette', self.open_color_palette,
                        'Ctrl+L', 'color_line', 'Set custom colors for each class')

        create_mode = action(get_str('crtBox'), self.set_create_mode,
                             'w', 'new', get_str('crtBoxDetail'), enabled=False)
        edit_mode = action(get_str('editBox'), self.set_edit_mode,
                           'Ctrl+J', 'edit', get_str('editBoxDetail'), enabled=False)

        create = action(get_str('crtBox'), self.create_shape,
                        'w', 'new', get_str('crtBoxDetail'), enabled=False)
        delete = action(get_str('delBox'), self.delete_selected_shape,
                        'Space', 'delete', get_str('delBoxDetail'), enabled=False)
        copy = action(get_str('dupBox'), self.copy_selected_shape,
                      None, 'copy', get_str('dupBoxDetail'),
                      enabled=False)

        # New copy/paste actions for selection
        copy_selection = action('Copy Selected', self.copy_selected_boxes,
                               'Ctrl+C', 'copy', 'Copy selected boxes to clipboard',
                               enabled=False)
        paste_selection = action('Paste', self.paste_selected_boxes,
                                'Ctrl+V', 'copy', 'Paste boxes from clipboard',
                                enabled=False)

        advanced_mode = action(get_str('advancedMode'), self.toggle_advanced_mode,
                               'Ctrl+Shift+A', 'expert', get_str('advancedModeDetail'),
                               checkable=True)

        hide_all = action(get_str('hideAllBox'), partial(self.toggle_polygons, False),
                          'Ctrl+H', 'hide', get_str('hideAllBoxDetail'),
                          enabled=False)
        show_all = action(get_str('showAllBox'), partial(self.toggle_polygons, True),
                          None, 'hide', get_str('showAllBoxDetail'),
                          enabled=False)
        select_all = action('Select All Boxes', self.select_all_boxes,
                           'Ctrl+A', 'hide', 'Select all boxes',
                           enabled=False)

        help_default = action(get_str('tutorialDefault'), self.show_default_tutorial_dialog, None, 'help', get_str('tutorialDetail'))
        show_info = action(get_str('info'), self.show_info_dialog, None, 'help', get_str('info'))
        show_shortcut = action(get_str('shortcut'), self.show_shortcuts_dialog, None, 'help', get_str('shortcut'))

        zoom = QWidgetAction(self)
        zoom.setDefaultWidget(self.zoom_widget)
        self.zoom_widget.setWhatsThis(
            u"Zoom in or out of the image. Also accessible with"
            " %s and %s from the canvas." % (format_shortcut("Ctrl+[-+]"),
                                             format_shortcut("Ctrl+Wheel")))
        self.zoom_widget.setEnabled(False)

        zoom_in = action(get_str('zoomin'), partial(self.add_zoom, 10),
                         'Ctrl++', 'zoom-in', get_str('zoominDetail'), enabled=False)
        zoom_out = action(get_str('zoomout'), partial(self.add_zoom, -10),
                          'Ctrl+-', 'zoom-out', get_str('zoomoutDetail'), enabled=False)
        zoom_org = action(get_str('originalsize'), partial(self.set_zoom, 100),
                          'Ctrl+=', 'zoom', get_str('originalsizeDetail'), enabled=False)
        fit_window = action(get_str('fitWin'), self.set_fit_window,
                            'Ctrl+F', 'fit-window', get_str('fitWinDetail'),
                            checkable=True, enabled=False)
        fit_width = action(get_str('fitWidth'), self.set_fit_width,
                           'Ctrl+Shift+F', 'fit-width', get_str('fitWidthDetail'),
                           checkable=True, enabled=False)
        # Group zoom controls into a list for easier toggling.
        zoom_actions = (self.zoom_widget, zoom_in, zoom_out,
                        zoom_org, fit_window, fit_width)
        self.zoom_mode = self.FIT_WINDOW  # Default to fit window mode
        fit_window.setChecked(True)  # Check fit window by default
        self.scalers = {
            self.FIT_WINDOW: self.scale_fit_window,
            self.FIT_WIDTH: self.scale_fit_width,
            # Set to one to scale to 100% when loading files.
            self.MANUAL_ZOOM: lambda: 1,
        }

        edit = action(get_str('editLabel'), self.edit_label,
                      'Ctrl+E', 'edit', get_str('editLabelDetail'),
                      enabled=False)
        # edit_button removed for cleaner UI

        shape_line_color = action(get_str('shapeLineColor'), self.choose_shape_line_color,
                                  icon='color_line', tip=get_str('shapeLineColorDetail'),
                                  enabled=False)
        shape_fill_color = action(get_str('shapeFillColor'), self.choose_shape_fill_color,
                                  icon='color', tip=get_str('shapeFillColorDetail'),
                                  enabled=False)

        labels = self.dock.toggleViewAction()
        labels.setText(get_str('showHide'))
        labels.setShortcut('Ctrl+Shift+L')

        # Label list context menu.
        label_menu = QMenu()
        add_actions(label_menu, (edit, delete))
        self.label_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.label_list.customContextMenuRequested.connect(
            self.pop_label_list_menu)

        # Draw squares/rectangles
        self.draw_squares_option = QAction(get_str('drawSquares'), self)
        self.draw_squares_option.setShortcut('Ctrl+Shift+R')
        self.draw_squares_option.setCheckable(True)
        self.draw_squares_option.setChecked(settings.get(SETTING_DRAW_SQUARE, False))
        self.draw_squares_option.triggered.connect(self.toggle_draw_square)

        # Store actions for further handling.
        self.actions = Struct(save=save, save_format=save_format, saveAs=save_as, open=open, close=close, resetAll=reset_all, deleteImg=delete_image,
                              lineColor=color1, create=create, delete=delete, edit=edit, copy=copy,
                              copySelection=copy_selection, pasteSelection=paste_selection, selectAll=select_all,
                              createMode=create_mode, editMode=edit_mode, advancedMode=advanced_mode,
                              shapeLineColor=shape_line_color, shapeFillColor=shape_fill_color,
                              zoom=zoom, zoomIn=zoom_in, zoomOut=zoom_out, zoomOrg=zoom_org,
                              fitWindow=fit_window, fitWidth=fit_width,
                              zoomActions=zoom_actions,
                              fileMenuActions=(
                                  open, open_dir, save, save_as, close, reset_all, quit),
                              beginner=(), advanced=(),
                              editMenu=(edit, copy, delete, copy_selection, paste_selection,
                                        None, color1, self.draw_squares_option),
                              beginnerContext=(create, edit, copy, delete),
                              advancedContext=(create_mode, edit_mode, edit, copy,
                                               delete, shape_line_color, shape_fill_color),
                              onLoadActive=(
                                  close, create, create_mode, edit_mode, paste_selection),
                              onShapesPresent=(save_as, hide_all, show_all, select_all))

        self.menus = Struct(
            file=self.menu(get_str('menu_file')),
            edit=self.menu(get_str('menu_edit')),
            view=self.menu(get_str('menu_view')),
            help=self.menu(get_str('menu_help')),
            recentFiles=QMenu(get_str('menu_openRecent')),
            labelList=label_menu)

        # Auto saving : Enable auto saving if pressing next
        self.auto_saving = QAction(get_str('autoSaveMode'), self)
        self.auto_saving.setCheckable(True)
        self.auto_saving.setChecked(settings.get(SETTING_AUTO_SAVE, True))
        # Sync single class mode from PR#106
        self.single_class_mode = QAction(get_str('singleClsMode'), self)
        self.single_class_mode.setCheckable(True)
        self.single_class_mode.setChecked(settings.get(SETTING_SINGLE_CLASS, False))

        # Action to toggle use default label checkbox
        self.toggle_use_default_label = QAction('Toggle Use Default Label', self)
        self.toggle_use_default_label.setShortcut("Ctrl+Shift+C")
        self.toggle_use_default_label.setCheckable(True)
        self.toggle_use_default_label.setChecked(False)
        self.toggle_use_default_label.triggered.connect(self.toggle_use_default_label_action)
        self.lastLabel = None
        # Add option to enable/disable labels being displayed at the top of bounding boxes
        self.display_label_option = QAction(get_str('displayLabel'), self)
        self.display_label_option.setShortcut("Ctrl+Shift+P")
        self.display_label_option.setCheckable(True)
        self.display_label_option.setChecked(settings.get(SETTING_PAINT_LABEL, False))
        self.display_label_option.triggered.connect(self.toggle_paint_labels_option)

        # Color palette option
        color_palette = action('Color Palette', self.open_color_palette,
                              None, 'color', 'Set custom colors for each class')

        add_actions(self.menus.file,
                    (open, open_dir, change_save_dir, open_annotation, copy_prev_bounding, self.menus.recentFiles, save, save_format, save_as, close, reset_all, delete_image, quit))
        add_actions(self.menus.help, (help_default, show_info, show_shortcut))
        add_actions(self.menus.view, (
            self.auto_saving,
            self.single_class_mode,
            self.display_label_option,
            self.toggle_use_default_label,
            color_palette, None,
            labels, advanced_mode, None,
            hide_all, show_all, select_all, None,
            zoom_in, zoom_out, zoom_org, None,
            fit_window, fit_width))

        self.menus.file.aboutToShow.connect(self.update_file_menu)

        # Custom context menu for the canvas widget:
        add_actions(self.canvas.menus[0], self.actions.beginnerContext)
        add_actions(self.canvas.menus[1], (
            action('&Copy here', self.copy_shape),
            action('&Move here', self.move_shape)))

        self.tools = self.toolbar('Tools')
        self.actions.beginner = (
            open_dir, verify, save, None,
            fit_window)

        self.actions.advanced = (
            open_dir, save, None,
            fit_window)

        # Add navigation shortcuts even though buttons aren't in toolbar
        self.addAction(open_next_image)
        self.addAction(open_prev_image)

        # Status bar will show version label permanently, no startup message needed
        self.statusBar().show()

        # Override showMessage to disable all status messages
        self._original_showMessage = self.statusBar().showMessage
        self.statusBar().showMessage = lambda *args, **kwargs: None  # Disable all status messages

        # Application state.
        self.image = QImage()
        self.file_path = ustr(default_filename)
        self.last_open_dir = None
        self.recent_files = []
        self.max_recent = 7
        self.line_color = None
        self.fill_color = None
        self.zoom_level = 100
        self.fit_window = False
        # Add Chris
        self.difficult = False

        # Fix the compatible issue for qt4 and qt5. Convert the QStringList to python list
        if settings.get(SETTING_RECENT_FILES):
            if have_qstring():
                recent_file_qstring_list = settings.get(SETTING_RECENT_FILES)
                self.recent_files = [ustr(i) for i in recent_file_qstring_list]
            else:
                self.recent_files = recent_file_qstring_list = settings.get(SETTING_RECENT_FILES)

        size = settings.get(SETTING_WIN_SIZE, QSize(600, 500))
        position = QPoint(0, 0)
        saved_position = settings.get(SETTING_WIN_POSE, position)
        # Fix the multiple monitors issue
        for i in range(QApplication.desktop().screenCount()):
            if QApplication.desktop().availableGeometry(i).contains(saved_position):
                position = saved_position
                break
        self.resize(size)
        self.move(position)
        save_dir = ustr(settings.get(SETTING_SAVE_DIR, None))
        self.last_open_dir = ustr(settings.get(SETTING_LAST_OPEN_DIR, None))
        if self.default_save_dir is None and save_dir is not None and os.path.exists(save_dir):
            self.default_save_dir = save_dir
            # Version label is always visible, no need for startup message

        self.restoreState(settings.get(SETTING_WIN_STATE, QByteArray()))
        # Hide file dock after state restoration
        self.file_dock.setVisible(False)
        self.file_dock.hide()
        Shape.line_color = self.line_color = QColor(settings.get(SETTING_LINE_COLOR, DEFAULT_LINE_COLOR))
        Shape.fill_color = self.fill_color = QColor(settings.get(SETTING_FILL_COLOR, DEFAULT_FILL_COLOR))
        self.canvas.set_drawing_color(self.line_color)
        # Add chris
        Shape.difficult = self.difficult

        def xbool(x):
            if isinstance(x, QVariant):
                return x.toBool()
            return bool(x)

        if xbool(settings.get(SETTING_ADVANCE_MODE, False)):
            self.actions.advancedMode.setChecked(True)
            self.toggle_advanced_mode()

        # Populate the File menu dynamically.
        self.update_file_menu()

        # Since loading the file may take some time, make sure it runs in the background.
        if self.file_path and os.path.isdir(self.file_path):
            self.queue_event(partial(self.import_dir_images, self.file_path or ""))
        elif self.file_path:
            self.queue_event(partial(self.load_file, self.file_path or ""))

        # Callbacks:
        self.zoom_widget.valueChanged.connect(self.paint_canvas)

        self.populate_mode_actions()

        # Display version at the left of status bar
        self.version_label = QLabel('v3.1')
        self.version_label.setStyleSheet("QLabel { color: #666; font-weight: bold; margin-right: 20px; }")
        self.statusBar().addWidget(self.version_label)

        # Coordinates display disabled - only version shown
        self.label_coordinates = QLabel('')  # Keep for compatibility but don't add to status bar

        # Open Dir if default file
        if self.file_path and os.path.isdir(self.file_path):
            self.open_dir_dialog(dir_path=self.file_path, silent=True)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Control:
            self.canvas.set_drawing_shape_to_square(False)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Control:
            # Draw rectangle if Ctrl is pressed
            self.canvas.set_drawing_shape_to_square(True)

    # Support Functions #
    def set_format(self, save_format):
        if save_format == FORMAT_PASCALVOC:
            self.actions.save_format.setText(FORMAT_PASCALVOC)
            self.actions.save_format.setIcon(new_icon("format_voc"))
            self.label_file_format = LabelFileFormat.PASCAL_VOC
            LabelFile.suffix = XML_EXT

        elif save_format == FORMAT_YOLO:
            self.actions.save_format.setText(FORMAT_YOLO)
            self.actions.save_format.setIcon(new_icon("format_yolo"))
            self.label_file_format = LabelFileFormat.YOLO
            LabelFile.suffix = TXT_EXT

        elif save_format == FORMAT_CREATEML:
            self.actions.save_format.setText(FORMAT_CREATEML)
            self.actions.save_format.setIcon(new_icon("format_createml"))
            self.label_file_format = LabelFileFormat.CREATE_ML
            LabelFile.suffix = JSON_EXT

    def change_format(self):
        if self.label_file_format == LabelFileFormat.PASCAL_VOC:
            self.set_format(FORMAT_YOLO)
        elif self.label_file_format == LabelFileFormat.YOLO:
            self.set_format(FORMAT_CREATEML)
        elif self.label_file_format == LabelFileFormat.CREATE_ML:
            self.set_format(FORMAT_PASCALVOC)
        else:
            raise ValueError('Unknown label file format.')
        self.set_dirty()

    def no_shapes(self):
        return not self.items_to_shapes

    def toggle_advanced_mode(self, value=True):
        self._beginner = not value
        self.canvas.set_editing(True)
        self.populate_mode_actions()
        # edit_button removed for cleaner UI
        if value:
            self.actions.createMode.setEnabled(True)
            self.actions.editMode.setEnabled(False)
            self.dock.setFeatures(self.dock.features() | self.dock_features)
        else:
            self.dock.setFeatures(self.dock.features() ^ self.dock_features)

    def populate_mode_actions(self):
        if self.beginner():
            tool, menu = self.actions.beginner, self.actions.beginnerContext
        else:
            tool, menu = self.actions.advanced, self.actions.advancedContext
        self.tools.clear()
        add_actions(self.tools, tool)
        self.canvas.menus[0].clear()
        add_actions(self.canvas.menus[0], menu)
        self.menus.edit.clear()
        actions = (self.actions.create,) if self.beginner()\
            else (self.actions.createMode, self.actions.editMode)
        add_actions(self.menus.edit, actions + self.actions.editMenu)

    def set_beginner(self):
        self.tools.clear()
        add_actions(self.tools, self.actions.beginner)

    def set_advanced(self):
        self.tools.clear()
        add_actions(self.tools, self.actions.advanced)

    def set_dirty(self):
        self.dirty = True
        self.actions.save.setEnabled(True)

    def set_clean(self):
        self.dirty = False
        self.actions.save.setEnabled(False)
        self.actions.create.setEnabled(True)

    def toggle_actions(self, value=True):
        """Enable/Disable widgets which depend on an opened image."""
        for z in self.actions.zoomActions:
            z.setEnabled(value)
        for action in self.actions.onLoadActive:
            action.setEnabled(value)

    def queue_event(self, function):
        QTimer.singleShot(0, function)

    def status(self, message, delay=5000):
        self.statusBar().showMessage(message, delay)

    def reset_state(self):
        self.items_to_shapes.clear()
        self.shapes_to_items.clear()
        self.label_list.clear()
        self.file_path = None
        self.image_data = None
        self.label_file = None
        self.canvas.reset_state()
        self.label_coordinates.clear()

    def current_item(self):
        items = self.label_list.selectedItems()
        if items:
            return items[0]
        return None

    def add_recent_file(self, file_path):
        if file_path in self.recent_files:
            self.recent_files.remove(file_path)
        elif len(self.recent_files) >= self.max_recent:
            self.recent_files.pop()
        self.recent_files.insert(0, file_path)

    def beginner(self):
        return self._beginner

    def advanced(self):
        return not self.beginner()

    def show_tutorial_dialog(self, browser='default', link=None):
        if link is None:
            link = self.screencast

        if browser.lower() == 'default':
            wb.open(link, new=2)
        elif browser.lower() == 'chrome' and self.os_name == 'Windows':
            if shutil.which(browser.lower()):  # 'chrome' not in wb._browsers in windows
                wb.register('chrome', None, wb.BackgroundBrowser('chrome'))
            else:
                chrome_path="D:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe"
                if os.path.isfile(chrome_path):
                    wb.register('chrome', None, wb.BackgroundBrowser(chrome_path))
            try:
                wb.get('chrome').open(link, new=2)
            except:
                wb.open(link, new=2)
        elif browser.lower() in wb._browsers:
            wb.get(browser.lower()).open(link, new=2)

    def show_default_tutorial_dialog(self):
        self.show_tutorial_dialog(browser='default')

    def show_info_dialog(self):
        from libs.__init__ import __version__
        msg = u'Name:{0} \nApp Version:{1} \n{2} '.format(__appname__, __version__, sys.version_info)
        QMessageBox.information(self, u'Information', msg)

    def show_shortcuts_dialog(self):
        self.show_tutorial_dialog(browser='default', link='https://github.com/tzutalin/labelImg#Hotkeys')

    def create_shape(self):
        assert self.beginner()
        self.canvas.set_editing(False)
        self.actions.create.setEnabled(False)

    def toggle_drawing_sensitive(self, drawing=True):
        """In the middle of drawing, toggling between modes should be disabled."""
        self.actions.editMode.setEnabled(not drawing)
        if not drawing and self.beginner():
            # Cancel creation.
            print('Cancel creation.')
            self.canvas.set_editing(True)
            self.canvas.restore_cursor()
            self.actions.create.setEnabled(True)

    def toggle_draw_mode(self, edit=True):
        self.canvas.set_editing(edit)
        self.actions.createMode.setEnabled(edit)
        self.actions.editMode.setEnabled(not edit)

    def toggle_use_default_label_action(self):
        """Toggle the use default label checkbox via Ctrl+Shift+S shortcut."""
        current_state = self.use_default_label_checkbox.isChecked()
        self.use_default_label_checkbox.setChecked(not current_state)

    def set_create_mode(self):
        assert self.advanced()
        self.toggle_draw_mode(False)

    def set_edit_mode(self):
        assert self.advanced()
        self.toggle_draw_mode(True)
        self.label_selection_changed()

    def update_file_menu(self):
        curr_file_path = self.file_path

        def exists(filename):
            return os.path.exists(filename)
        menu = self.menus.recentFiles
        menu.clear()
        files = [f for f in self.recent_files if f !=
                 curr_file_path and exists(f)]
        for i, f in enumerate(files):
            icon = new_icon('labels')
            action = QAction(
                icon, '&%d %s' % (i + 1, QFileInfo(f).fileName()), self)
            action.triggered.connect(partial(self.load_recent, f))
            menu.addAction(action)

    def pop_label_list_menu(self, point):
        self.menus.labelList.exec_(self.label_list.mapToGlobal(point))

    def edit_label(self):
        if not self.canvas.editing():
            return
        item = self.current_item()
        if not item:
            return
        text = self.label_dialog.pop_up(item.text())
        if text is not None:
            item.setText(text)
            # Use custom color if set, otherwise generate color
            item.setBackground(self.class_colors.get(text, generate_color_by_text(text)))
            self.set_dirty()
            self.update_combo_box()

    # Tzutalin 20160906 : Add file list and dock to move faster
    def file_item_double_clicked(self, item=None):
        self.cur_img_idx = self.m_img_list.index(ustr(item.text()))
        filename = self.m_img_list[self.cur_img_idx]
        if filename:
            self.load_file(filename)

    def get_last_visible_label_item(self):
        """Get the last visible (non-hidden) item in the label list."""
        for i in range(self.label_list.count() - 1, -1, -1):
            item = self.label_list.item(i)
            if not item.isHidden():
                return item
        return None

    # Add chris
    def button_state(self, item=None):
        """ Function to handle difficult examples
        Update on each object """
        if not self.canvas.editing():
            return

        item = self.current_item()
        if not item:  # If not selected Item, take the last visible one
            item = self.get_last_visible_label_item()

        # diffc_button removed - difficult always set to False
        difficult = False

        try:
            shape = self.items_to_shapes[item]
        except:
            pass
        # Checked and Update
        try:
            if difficult != shape.difficult:
                shape.difficult = difficult
                self.set_dirty()
            else:  # User probably changed item visibility
                self.canvas.set_shape_visible(shape, item.checkState() == Qt.Checked)
        except:
            pass

    def bbox_thickness_changed(self, value):
        """Update the bounding box line thickness."""
        from libs.shape import Shape
        Shape.line_width = value
        self.canvas.update()

    def class_visibility_item_clicked(self, item):
        """Toggle checkbox when clicking anywhere on the class visibility item."""
        if item.checkState() == Qt.Checked:
            item.setCheckState(Qt.Unchecked)
        else:
            item.setCheckState(Qt.Checked)

    def class_visibility_changed(self, item=None):
        """Filter label visibility based on multi-selected classes (persistent across images)."""
        # Collect all checked classes from the visibility list
        selected_classes = []
        show_all = False

        for i in range(self.class_visibility_list.count()):
            vis_item = self.class_visibility_list.item(i)
            if vis_item.checkState() == Qt.Checked:
                class_name = vis_item.data(Qt.UserRole)
                if class_name == "":  # "Show All" option
                    show_all = True
                    break
                selected_classes.append(class_name)

        # Hide/show items in label list based on filter, and update canvas visibility
        for i in range(self.label_list.count()):
            label_item = self.label_list.item(i)
            label_text = label_item.text()

            # Show if "Show All" is checked OR label is in selected classes
            should_show = show_all or label_text in selected_classes

            # Hide or show the item in the list
            label_item.setHidden(not should_show)

            # Update canvas visibility and checkbox state
            if label_item in self.items_to_shapes:
                shape = self.items_to_shapes[label_item]
                if should_show:
                    # Only show items that match the filter and are checked
                    label_item.setCheckState(Qt.Checked)
                    self.canvas.set_shape_visible(shape, True)
                else:
                    # Hide shapes that don't match the filter
                    self.canvas.set_shape_visible(shape, False)

        self.canvas.update()

    def update_class_visibility_list(self, class_name):
        """Add a class to the visibility list if it doesn't exist."""
        # Check if class already exists in list
        for i in range(self.class_visibility_list.count()):
            if self.class_visibility_list.item(i).data(Qt.UserRole) == class_name:
                return  # Already exists

        # Add new class to list with checkbox
        new_item = QListWidgetItem(class_name)
        new_item.setFlags(new_item.flags() | Qt.ItemIsUserCheckable)
        new_item.setCheckState(Qt.Unchecked)  # Default unchecked for new classes
        new_item.setData(Qt.UserRole, class_name)
        self.class_visibility_list.addItem(new_item)

    # React to canvas signals.
    def shape_selection_changed(self, selected=False):
        if self._no_selection_slot:
            self._no_selection_slot = False
        else:
            # Block signals to prevent recursion when updating label list
            self.label_list.blockSignals(True)

            # Update label list selection for all selected shapes
            self.label_list.clearSelection()
            for shape in self.canvas.selected_shapes:
                if shape in self.shapes_to_items:
                    self.shapes_to_items[shape].setSelected(True)

            # Fallback to single selection
            shape = self.canvas.selected_shape
            if shape and shape not in self.canvas.selected_shapes:
                if shape in self.shapes_to_items:
                    self.shapes_to_items[shape].setSelected(True)

            # Re-enable signals
            self.label_list.blockSignals(False)

        self.actions.delete.setEnabled(selected)
        self.actions.copy.setEnabled(selected)
        self.actions.copySelection.setEnabled(selected)
        self.actions.edit.setEnabled(selected)
        self.actions.shapeLineColor.setEnabled(selected)
        self.actions.shapeFillColor.setEnabled(selected)

    def add_label(self, shape):
        shape.paint_label = self.display_label_option.isChecked()
        item = HashableQListWidgetItem(shape.label)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        # Use custom color if set, otherwise generate color
        item.setBackground(self.class_colors.get(shape.label, generate_color_by_text(shape.label)))
        self.items_to_shapes[item] = shape
        self.shapes_to_items[shape] = item
        self.label_list.addItem(item)
        for action in self.actions.onShapesPresent:
            action.setEnabled(True)

        # Update the NEW persistent class visibility list
        self.update_class_visibility_list(shape.label)

        self.update_combo_box()

    def remove_label(self, shape):
        if shape is None:
            # print('rm empty label')
            return
        item = self.shapes_to_items[shape]
        self.label_list.takeItem(self.label_list.row(item))
        del self.shapes_to_items[shape]
        del self.items_to_shapes[item]
        self.update_combo_box()

    def load_labels(self, shapes):
        s = []
        for label, points, line_color, fill_color, difficult in shapes:
            shape = Shape(label=label)
            for x, y in points:

                # Ensure the labels are within the bounds of the image. If not, fix them.
                x, y, snapped = self.canvas.snap_point_to_canvas(x, y)
                if snapped:
                    self.set_dirty()

                shape.add_point(QPointF(x, y))
            shape.difficult = difficult
            shape.close()
            s.append(shape)

            if line_color:
                shape.line_color = QColor(*line_color)
            else:
                # Use custom color if set, otherwise generate color
                shape.line_color = self.class_colors.get(label, generate_color_by_text(label))

            if fill_color:
                shape.fill_color = QColor(*fill_color)
            else:
                # Use custom color if set, otherwise generate color
                shape.fill_color = self.class_colors.get(label, generate_color_by_text(label))

            self.add_label(shape)
        self.update_combo_box()

        # Disable canvas updates to prevent flash before filter is applied
        self.canvas.setUpdatesEnabled(False)
        self.canvas.load_shapes(s)

        # Re-apply the persistent class visibility filter to newly loaded labels
        self.class_visibility_changed()

        # Re-enable canvas updates and repaint once with filter applied
        self.canvas.setUpdatesEnabled(True)
        self.canvas.update()

    def update_combo_box(self):
        # Old combo_box removed - this method kept as no-op for compatibility
        pass

    def save_labels(self, annotation_file_path):
        annotation_file_path = ustr(annotation_file_path)
        if self.label_file is None:
            self.label_file = LabelFile()
            self.label_file.verified = self.canvas.verified

        def format_shape(s):
            return dict(label=s.label,
                        line_color=s.line_color.getRgb(),
                        fill_color=s.fill_color.getRgb(),
                        points=[(p.x(), p.y()) for p in s.points],
                        # add chris
                        difficult=s.difficult)

        shapes = [format_shape(shape) for shape in self.canvas.shapes]
        # Can add different annotation formats here
        try:
            if self.label_file_format == LabelFileFormat.PASCAL_VOC:
                if annotation_file_path[-4:].lower() != ".xml":
                    annotation_file_path += XML_EXT
                self.label_file.save_pascal_voc_format(annotation_file_path, shapes, self.file_path, self.image_data,
                                                       self.line_color.getRgb(), self.fill_color.getRgb())
            elif self.label_file_format == LabelFileFormat.YOLO:
                if annotation_file_path[-4:].lower() != ".txt":
                    annotation_file_path += TXT_EXT
                self.label_file.save_yolo_format(annotation_file_path, shapes, self.file_path, self.image_data, self.label_hist,
                                                 self.line_color.getRgb(), self.fill_color.getRgb())
            elif self.label_file_format == LabelFileFormat.CREATE_ML:
                if annotation_file_path[-5:].lower() != ".json":
                    annotation_file_path += JSON_EXT
                self.label_file.save_create_ml_format(annotation_file_path, shapes, self.file_path, self.image_data,
                                                      self.label_hist, self.line_color.getRgb(), self.fill_color.getRgb())
            else:
                self.label_file.save(annotation_file_path, shapes, self.file_path, self.image_data,
                                     self.line_color.getRgb(), self.fill_color.getRgb())
            print('Image:{0} -> Annotation:{1}'.format(self.file_path, annotation_file_path))
            return True
        except LabelFileError as e:
            self.error_message(u'Error saving label data', u'<b>%s</b>' % e)
            return False

    def copy_selected_shape(self):
        self.add_label(self.canvas.copy_selected_shape())
        # fix copy and delete
        self.shape_selection_changed(True)

    def combo_selection_changed(self, index):
        # Old combo_box removed - this method kept as no-op for compatibility
        pass

    def label_selection_changed(self):
        items = self.label_list.selectedItems()
        if items and self.canvas.editing():
            self._no_selection_slot = True
            try:
                # Support multiple selection
                if len(items) == 1:
                    item = items[0]
                    if item in self.items_to_shapes:
                        self.canvas.select_shape(self.items_to_shapes[item])
                        shape = self.items_to_shapes[item]
                        # diffc_button removed - no longer needed
                else:
                    # Multiple items selected - update selection without emitting signals
                    for shape in self.canvas.shapes:
                        shape.selected = False
                    self.canvas.selected_shapes = []

                    for item in items:
                        if item in self.items_to_shapes:
                            shape = self.items_to_shapes[item]
                            shape.selected = True
                            self.canvas.selected_shapes.append(shape)

                    if self.canvas.selected_shapes:
                        self.canvas.selected_shape = self.canvas.selected_shapes[0]
                    else:
                        self.canvas.selected_shape = None

                    self.canvas.update()
            finally:
                # Always reset the flag, even if an error occurs
                self._no_selection_slot = False

    def label_item_changed(self, item):
        shape = self.items_to_shapes[item]
        label = item.text()
        if label != shape.label:
            shape.label = item.text()
            # Use custom color if set, otherwise generate color
            shape.line_color = self.class_colors.get(shape.label, generate_color_by_text(shape.label))
            self.set_dirty()
        else:  # User probably changed item visibility
            self.canvas.set_shape_visible(shape, item.checkState() == Qt.Checked)

    # Callback functions:
    def new_shape(self):
        """Pop-up and give focus to the label editor.

        position MUST be in global coordinates.
        """
        if not self.use_default_label_checkbox.isChecked() or not self.default_label_combo.currentText():
            if len(self.label_hist) > 0:
                self.label_dialog = LabelDialog(
                    parent=self, list_item=self.label_hist)

            # Sync single class mode from PR#106
            if self.single_class_mode.isChecked() and self.lastLabel:
                text = self.lastLabel
            else:
                text = self.label_dialog.pop_up(text=self.prev_label_text)
                self.lastLabel = text
        else:
            text = self.default_label_combo.currentText()

        # diffc_button removed - no longer needed
        if text is not None:
            # Check if this is a new class and ask for confirmation
            if text not in self.label_hist:
                reply = QMessageBox.question(
                    self,
                    'Add New Class',
                    f'The class "{text}" does not exist.\n\nDo you want to add this new class?',
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )

                if reply == QMessageBox.No:
                    # User cancelled, reset the drawing
                    self.canvas.reset_all_lines()
                    return

            self.prev_label_text = text
            # Use custom color if set, otherwise generate color
            generate_color = self.class_colors.get(text, generate_color_by_text(text))
            shape = self.canvas.set_last_label(text, generate_color, generate_color)
            self.add_label(shape)
            if self.beginner():  # Switch to edit mode.
                self.canvas.set_editing(True)
                self.actions.create.setEnabled(True)
            else:
                self.actions.editMode.setEnabled(True)
            self.set_dirty()

            if text not in self.label_hist:
                self.label_hist.append(text)
                # Add to combo only if not already there
                if self.default_label_combo.findText(text) == -1:
                    self.default_label_combo.addItem(text)
        else:
            # self.canvas.undoLastLine()
            self.canvas.reset_all_lines()

    def scroll_request(self, delta, orientation):
        units = - delta / (8 * 15)
        bar = self.scroll_bars[orientation]
        bar.setValue(bar.value() + bar.singleStep() * units)

    def set_zoom(self, value):
        self.actions.fitWidth.setChecked(False)
        self.actions.fitWindow.setChecked(False)
        self.zoom_mode = self.MANUAL_ZOOM
        self.zoom_widget.setValue(value)

    def add_zoom(self, increment=10):
        self.set_zoom(self.zoom_widget.value() + increment)

    def zoom_request(self, delta):
        # get the current scrollbar positions
        # calculate the percentages ~ coordinates
        h_bar = self.scroll_bars[Qt.Horizontal]
        v_bar = self.scroll_bars[Qt.Vertical]

        # get the current maximum, to know the difference after zooming
        h_bar_max = h_bar.maximum()
        v_bar_max = v_bar.maximum()

        # get the cursor position and canvas size
        # calculate the desired movement from 0 to 1
        # where 0 = move left
        #       1 = move right
        # up and down analogous
        cursor = QCursor()
        pos = cursor.pos()
        relative_pos = QWidget.mapFromGlobal(self, pos)

        cursor_x = relative_pos.x()
        cursor_y = relative_pos.y()

        w = self.scroll_area.width()
        h = self.scroll_area.height()

        # the scaling from 0 to 1 has some padding
        # you don't have to hit the very leftmost pixel for a maximum-left movement
        margin = 0.1
        move_x = (cursor_x - margin * w) / (w - 2 * margin * w)
        move_y = (cursor_y - margin * h) / (h - 2 * margin * h)

        # clamp the values from 0 to 1
        move_x = min(max(move_x, 0), 1)
        move_y = min(max(move_y, 0), 1)

        # zoom in
        units = delta / (8 * 15)
        scale = 10
        self.add_zoom(scale * units)

        # get the difference in scrollbar values
        # this is how far we can move
        d_h_bar_max = h_bar.maximum() - h_bar_max
        d_v_bar_max = v_bar.maximum() - v_bar_max

        # get the new scrollbar values
        new_h_bar_value = h_bar.value() + move_x * d_h_bar_max
        new_v_bar_value = v_bar.value() + move_y * d_v_bar_max

        h_bar.setValue(new_h_bar_value)
        v_bar.setValue(new_v_bar_value)

    def set_fit_window(self, value=True):
        if value:
            self.actions.fitWidth.setChecked(False)
        self.zoom_mode = self.FIT_WINDOW if value else self.MANUAL_ZOOM
        self.adjust_scale()

    def set_fit_width(self, value=True):
        if value:
            self.actions.fitWindow.setChecked(False)
        self.zoom_mode = self.FIT_WIDTH if value else self.MANUAL_ZOOM
        self.adjust_scale()

    def toggle_polygons(self, value):
        for item, shape in self.items_to_shapes.items():
            item.setCheckState(Qt.Checked if value else Qt.Unchecked)

    def load_file(self, file_path=None):
        """Load the specified file, or the last opened file if None."""
        # Save current zoom settings to restore them after loading
        saved_zoom_mode = self.zoom_mode
        saved_zoom_value = self.zoom_widget.value()
        saved_h_scroll = self.scroll_bars[Qt.Horizontal].value()
        saved_v_scroll = self.scroll_bars[Qt.Vertical].value()

        self.reset_state()
        self.canvas.setEnabled(False)
        if file_path is None:
            file_path = self.settings.get(SETTING_FILENAME)

        # Make sure that filePath is a regular python string, rather than QString
        file_path = ustr(file_path)

        # Fix bug: An  index error after select a directory when open a new file.
        unicode_file_path = ustr(file_path)
        unicode_file_path = os.path.abspath(unicode_file_path)
        # Tzutalin 20160906 : Add file list and dock to move faster
        # Highlight the file item
        if unicode_file_path and self.file_list_widget.count() > 0:
            if unicode_file_path in self.m_img_list:
                index = self.m_img_list.index(unicode_file_path)
                file_widget_item = self.file_list_widget.item(index)
                file_widget_item.setSelected(True)
            else:
                self.file_list_widget.clear()
                self.m_img_list.clear()

        if unicode_file_path and os.path.exists(unicode_file_path):
            if LabelFile.is_label_file(unicode_file_path):
                try:
                    self.label_file = LabelFile(unicode_file_path)
                except LabelFileError as e:
                    self.error_message(u'Error opening file',
                                       (u"<p><b>%s</b></p>"
                                        u"<p>Make sure <i>%s</i> is a valid label file.")
                                       % (e, unicode_file_path))
                    self.status("Error reading %s" % unicode_file_path)
                    return False
                self.image_data = self.label_file.image_data
                self.line_color = QColor(*self.label_file.lineColor)
                self.fill_color = QColor(*self.label_file.fillColor)
                self.canvas.verified = self.label_file.verified
            else:
                # Load image:
                # read data first and store for saving into label file.
                self.image_data = read(unicode_file_path, None)
                self.label_file = None
                self.canvas.verified = False

            if isinstance(self.image_data, QImage):
                image = self.image_data
            else:
                image = QImage.fromData(self.image_data)
            if image.isNull():
                self.error_message(u'Error opening file',
                                   u"<p>Make sure <i>%s</i> is a valid image file." % unicode_file_path)
                self.status("Error reading %s" % unicode_file_path)
                return False
            # Don't show "Loaded" message - version label is always visible
            self.image = image
            self.file_path = unicode_file_path
            self.canvas.load_pixmap(QPixmap.fromImage(image))
            if self.label_file:
                self.load_labels(self.label_file.shapes)
            self.set_clean()
            self.canvas.setEnabled(True)

            # Restore zoom mode and settings without reset
            # Block signals to prevent double painting during setup
            self.zoom_widget.blockSignals(True)
            self.zoom_mode = saved_zoom_mode

            if saved_zoom_mode == self.MANUAL_ZOOM:
                # For manual zoom, restore exact zoom value
                self.zoom_widget.setValue(saved_zoom_value)
            else:
                # For fit window/fit width, recalculate scale for new image
                value = self.scalers[self.zoom_mode]()
                self.zoom_widget.setValue(int(100 * value))

            # Unblock signals and paint once with correct settings
            self.zoom_widget.blockSignals(False)
            self.paint_canvas()

            # Restore scroll positions for manual zoom
            # Use both immediate and delayed restoration to handle timing issues
            if saved_zoom_mode == self.MANUAL_ZOOM:
                self.scroll_bars[Qt.Horizontal].setValue(saved_h_scroll)
                self.scroll_bars[Qt.Vertical].setValue(saved_v_scroll)
                # Also set with slight delay in case canvas isn't fully ready
                QTimer.singleShot(100, lambda: self.scroll_bars[Qt.Horizontal].setValue(saved_h_scroll))
                QTimer.singleShot(100, lambda: self.scroll_bars[Qt.Vertical].setValue(saved_v_scroll))

            self.add_recent_file(self.file_path)
            self.toggle_actions(True)
            self.show_bounding_box_from_annotation_file(file_path)

            counter = self.counter_str()
            self.setWindowTitle(__appname__ + ' ' + file_path + ' ' + counter)

            # Default : select last visible item if there is at least one visible item
            last_visible_item = self.get_last_visible_label_item()
            if last_visible_item:
                self.label_list.setCurrentItem(last_visible_item)
                last_visible_item.setSelected(True)

            self.canvas.setFocus(True)
            return True
        return False

    def counter_str(self):
        """
        Converts image counter to string representation.
        """
        return '[{} / {}]'.format(self.cur_img_idx + 1, self.img_count)

    def show_bounding_box_from_annotation_file(self, file_path):
        if self.default_save_dir is not None:
            basename = os.path.basename(os.path.splitext(file_path)[0])
            xml_path = os.path.join(self.default_save_dir, basename + XML_EXT)
            txt_path = os.path.join(self.default_save_dir, basename + TXT_EXT)
            json_path = os.path.join(self.default_save_dir, basename + JSON_EXT)

            """Annotation file priority:
            PascalXML > YOLO
            """
            if os.path.isfile(xml_path):
                self.load_pascal_xml_by_filename(xml_path)
            elif os.path.isfile(txt_path):
                self.load_yolo_txt_by_filename(txt_path)
            elif os.path.isfile(json_path):
                self.load_create_ml_json_by_filename(json_path, file_path)

        else:
            xml_path = os.path.splitext(file_path)[0] + XML_EXT
            txt_path = os.path.splitext(file_path)[0] + TXT_EXT
            if os.path.isfile(xml_path):
                self.load_pascal_xml_by_filename(xml_path)
            elif os.path.isfile(txt_path):
                self.load_yolo_txt_by_filename(txt_path)

    def resizeEvent(self, event):
        if self.canvas and not self.image.isNull()\
           and self.zoom_mode != self.MANUAL_ZOOM:
            self.adjust_scale()
        super(MainWindow, self).resizeEvent(event)

    def paint_canvas(self):
        assert not self.image.isNull(), "cannot paint null image"
        self.canvas.scale = 0.01 * self.zoom_widget.value()
        self.canvas.label_font_size = 6  # Fixed font size
        self.canvas.adjustSize()
        self.canvas.update()

    def adjust_scale(self, initial=False):
        value = self.scalers[self.FIT_WINDOW if initial else self.zoom_mode]()
        self.zoom_widget.setValue(int(100 * value))

    def scale_fit_window(self):
        """Figure out the size of the pixmap in order to fit the main widget."""
        e = 2.0  # So that no scrollbars are generated.
        w1 = self.centralWidget().width() - e
        h1 = self.centralWidget().height() - e
        a1 = w1 / h1
        # Calculate a new scale value based on the pixmap's aspect ratio.
        w2 = self.canvas.pixmap.width() - 0.0
        h2 = self.canvas.pixmap.height() - 0.0
        a2 = w2 / h2
        return w1 / w2 if a2 >= a1 else h1 / h2

    def scale_fit_width(self):
        # The epsilon does not seem to work too well here.
        w = self.centralWidget().width() - 2.0
        return w / self.canvas.pixmap.width()

    def closeEvent(self, event):
        if not self.may_continue():
            event.ignore()
        settings = self.settings
        # If it loads images from dir, don't load it at the beginning
        if self.dir_name is None:
            settings[SETTING_FILENAME] = self.file_path if self.file_path else ''
        else:
            settings[SETTING_FILENAME] = ''

        settings[SETTING_WIN_SIZE] = self.size()
        settings[SETTING_WIN_POSE] = self.pos()
        settings[SETTING_WIN_STATE] = self.saveState()
        settings[SETTING_LINE_COLOR] = self.line_color
        settings[SETTING_FILL_COLOR] = self.fill_color
        settings[SETTING_RECENT_FILES] = self.recent_files
        settings[SETTING_ADVANCE_MODE] = not self._beginner
        if self.default_save_dir and os.path.exists(self.default_save_dir):
            settings[SETTING_SAVE_DIR] = ustr(self.default_save_dir)
        else:
            settings[SETTING_SAVE_DIR] = ''

        if self.last_open_dir and os.path.exists(self.last_open_dir):
            settings[SETTING_LAST_OPEN_DIR] = self.last_open_dir
        else:
            settings[SETTING_LAST_OPEN_DIR] = ''

        settings[SETTING_AUTO_SAVE] = self.auto_saving.isChecked()
        settings[SETTING_SINGLE_CLASS] = self.single_class_mode.isChecked()
        settings[SETTING_PAINT_LABEL] = self.display_label_option.isChecked()
        settings[SETTING_DRAW_SQUARE] = self.draw_squares_option.isChecked()
        settings[SETTING_LABEL_FILE_FORMAT] = self.label_file_format
        settings.save()

    def load_recent(self, filename):
        if self.may_continue():
            self.load_file(filename)

    def scan_all_images(self, folder_path):
        extensions = ['.%s' % fmt.data().decode("ascii").lower() for fmt in QImageReader.supportedImageFormats()]
        images = []

        for root, dirs, files in os.walk(folder_path):
            for file in files:
                if file.lower().endswith(tuple(extensions)):
                    relative_path = os.path.join(root, file)
                    path = ustr(os.path.abspath(relative_path))
                    images.append(path)
        natural_sort(images, key=lambda x: x.lower())
        return images

    def change_save_dir_dialog(self, _value=False):
        if self.default_save_dir is not None:
            path = ustr(self.default_save_dir)
        else:
            path = '.'

        dir_path = ustr(QFileDialog.getExistingDirectory(self,
                                                         '%s - Save annotations to the directory' % __appname__, path,  QFileDialog.ShowDirsOnly
                                                         | QFileDialog.DontResolveSymlinks))

        if dir_path is not None and len(dir_path) > 1:
            self.default_save_dir = dir_path

        self.statusBar().showMessage('%s . Annotation will be saved to %s' %
                                     ('Change saved folder', self.default_save_dir))
        self.statusBar().show()

    def open_annotation_dialog(self, _value=False):
        if self.file_path is None:
            self.statusBar().showMessage('Please select image first')
            self.statusBar().show()
            return

        path = os.path.dirname(ustr(self.file_path))\
            if self.file_path else '.'
        if self.label_file_format == LabelFileFormat.PASCAL_VOC:
            filters = "Open Annotation XML file (%s)" % ' '.join(['*.xml'])
            filename = ustr(QFileDialog.getOpenFileName(self, '%s - Choose a xml file' % __appname__, path, filters))
            if filename:
                if isinstance(filename, (tuple, list)):
                    filename = filename[0]
            self.load_pascal_xml_by_filename(filename)

    def open_dir_dialog(self, _value=False, dir_path=None, silent=False):
        if not self.may_continue():
            return

        default_open_dir_path = dir_path if dir_path else '.'
        if self.last_open_dir and os.path.exists(self.last_open_dir):
            default_open_dir_path = self.last_open_dir
        else:
            default_open_dir_path = os.path.dirname(self.file_path) if self.file_path else '.'
        if silent != True:
            target_dir_path = ustr(QFileDialog.getExistingDirectory(self,
                                                                    '%s - Open Directory' % __appname__, default_open_dir_path,
                                                                    QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks))
        else:
            target_dir_path = ustr(default_open_dir_path)
        self.last_open_dir = target_dir_path
        self.import_dir_images(target_dir_path)

    def import_dir_images(self, dir_path):
        if not self.may_continue() or not dir_path:
            return

        self.last_open_dir = dir_path
        self.dir_name = dir_path
        self.file_path = None
        self.file_list_widget.clear()

        # Auto-detect classes file in the directory
        possible_class_files = [
            os.path.join(dir_path, "classes.txt"),
            os.path.join(dir_path, "predefined_classes.txt")
        ]
        for class_file in possible_class_files:
            if os.path.exists(class_file):
                self.load_predefined_classes(class_file)
                break

        # Set default save directory to the opened directory
        self.default_save_dir = dir_path

        self.m_img_list = self.scan_all_images(dir_path)
        self.img_count = len(self.m_img_list)
        self.open_next_image()
        for imgPath in self.m_img_list:
            item = QListWidgetItem(imgPath)
            self.file_list_widget.addItem(item)

    def verify_image(self, _value=False):
        # Proceeding next image without dialog if having any label
        if self.file_path is not None:
            try:
                self.label_file.toggle_verify()
            except AttributeError:
                # If the labelling file does not exist yet, create if and
                # re-save it with the verified attribute.
                self.save_file()
                if self.label_file is not None:
                    self.label_file.toggle_verify()
                else:
                    return

            self.canvas.verified = self.label_file.verified
            self.paint_canvas()
            self.save_file()

    def auto_annotate_placeholder(self, _value=False):
        # Placeholder for Auto Annotate functionality
        # To be implemented later
        pass

    def open_prev_image(self, _value=False):
        # Proceeding prev image without dialog if having any label
        if self.auto_saving.isChecked():
            if self.default_save_dir is not None:
                if self.dirty is True:
                    self.save_file()
            else:
                self.change_save_dir_dialog()
                return

        if not self.may_continue():
            return

        if self.img_count <= 0:
            return

        if self.file_path is None:
            return

        if self.cur_img_idx - 1 >= 0:
            self.cur_img_idx -= 1
            filename = self.m_img_list[self.cur_img_idx]
            if filename:
                self.load_file(filename)

    def open_next_image(self, _value=False):
        # Proceeding prev image without dialog if having any label
        if self.auto_saving.isChecked():
            if self.default_save_dir is not None:
                if self.dirty is True:
                    self.save_file()
            else:
                self.change_save_dir_dialog()
                return

        if not self.may_continue():
            return

        if self.img_count <= 0:
            return

        filename = None
        if self.file_path is None:
            filename = self.m_img_list[0]
            self.cur_img_idx = 0
        else:
            if self.cur_img_idx + 1 < self.img_count:
                self.cur_img_idx += 1
                filename = self.m_img_list[self.cur_img_idx]

        if filename:
            self.load_file(filename)

    def go_to_image(self):
        """Navigate to a specific image by number (1-indexed)"""
        # Get the input text
        image_num_text = self.go_to_image_input.text().strip()

        if not image_num_text:
            return

        # Validate it's a number
        try:
            image_num = int(image_num_text)
        except ValueError:
            self.statusBar().showMessage('Please enter a valid number', 3000)
            return

        # Check if we have images loaded
        if self.img_count <= 0:
            self.statusBar().showMessage('No images loaded', 3000)
            return

        # Cap the number between 1 and img_count
        image_num = max(1, min(image_num, self.img_count))

        # Convert to 0-indexed
        target_idx = image_num - 1

        # Auto-save if enabled
        if self.auto_saving.isChecked():
            if self.default_save_dir is not None:
                if self.dirty is True:
                    self.save_file()
            else:
                self.change_save_dir_dialog()
                return

        if not self.may_continue():
            return

        # Navigate to the image
        self.cur_img_idx = target_idx
        filename = self.m_img_list[self.cur_img_idx]
        if filename:
            self.load_file(filename)
            # Clear the input field after successful navigation
            self.go_to_image_input.clear()

    def open_file(self, _value=False):
        if not self.may_continue():
            return
        path = os.path.dirname(ustr(self.file_path)) if self.file_path else '.'
        formats = ['*.%s' % fmt.data().decode("ascii").lower() for fmt in QImageReader.supportedImageFormats()]
        filters = "Image & Label files (%s)" % ' '.join(formats + ['*%s' % LabelFile.suffix])
        filename = QFileDialog.getOpenFileName(self, '%s - Choose Image or Label file' % __appname__, path, filters)
        if filename:
            if isinstance(filename, (tuple, list)):
                filename = filename[0]
            self.cur_img_idx = 0
            self.img_count = 1
            self.load_file(filename)

    def save_file(self, _value=False):
        if self.default_save_dir is not None and len(ustr(self.default_save_dir)):
            if self.file_path:
                image_file_name = os.path.basename(self.file_path)
                saved_file_name = os.path.splitext(image_file_name)[0]
                saved_path = os.path.join(ustr(self.default_save_dir), saved_file_name)
                self._save_file(saved_path)
        else:
            image_file_dir = os.path.dirname(self.file_path)
            image_file_name = os.path.basename(self.file_path)
            saved_file_name = os.path.splitext(image_file_name)[0]
            saved_path = os.path.join(image_file_dir, saved_file_name)
            self._save_file(saved_path if self.label_file
                            else self.save_file_dialog(remove_ext=False))

    def save_file_as(self, _value=False):
        assert not self.image.isNull(), "cannot save empty image"
        self._save_file(self.save_file_dialog())

    def save_file_dialog(self, remove_ext=True):
        caption = '%s - Choose File' % __appname__
        filters = 'File (*%s)' % LabelFile.suffix
        open_dialog_path = self.current_path()
        dlg = QFileDialog(self, caption, open_dialog_path, filters)
        dlg.setDefaultSuffix(LabelFile.suffix[1:])
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        filename_without_extension = os.path.splitext(self.file_path)[0]
        dlg.selectFile(filename_without_extension)
        dlg.setOption(QFileDialog.DontUseNativeDialog, False)
        if dlg.exec_():
            full_file_path = ustr(dlg.selectedFiles()[0])
            if remove_ext:
                return os.path.splitext(full_file_path)[0]  # Return file path without the extension.
            else:
                return full_file_path
        return ''

    def _save_file(self, annotation_file_path):
        if annotation_file_path and self.save_labels(annotation_file_path):
            self.set_clean()
            self.statusBar().showMessage('Saved to  %s' % annotation_file_path)
            self.statusBar().show()

    def close_file(self, _value=False):
        if not self.may_continue():
            return
        self.reset_state()
        self.set_clean()
        self.toggle_actions(False)
        self.canvas.setEnabled(False)
        self.actions.saveAs.setEnabled(False)

    def delete_image(self):
        delete_path = self.file_path
        if delete_path is not None:
            self.open_next_image()
            self.cur_img_idx -= 1
            self.img_count -= 1
            if os.path.exists(delete_path):
                os.remove(delete_path)
            self.import_dir_images(self.last_open_dir)

    def reset_all(self):
        self.settings.reset()
        self.close()
        process = QProcess()
        process.startDetached(os.path.abspath(__file__))

    def may_continue(self):
        if not self.dirty:
            return True
        else:
            discard_changes = self.discard_changes_dialog()
            if discard_changes == QMessageBox.No:
                return True
            elif discard_changes == QMessageBox.Yes:
                self.save_file()
                return True
            else:
                return False

    def discard_changes_dialog(self):
        yes, no, cancel = QMessageBox.Yes, QMessageBox.No, QMessageBox.Cancel
        msg = u'You have unsaved changes, would you like to save them and proceed?\nClick "No" to undo all changes.'
        return QMessageBox.warning(self, u'Attention', msg, yes | no | cancel)

    def error_message(self, title, message):
        return QMessageBox.critical(self, title,
                                    '<p><b>%s</b></p>%s' % (title, message))

    def current_path(self):
        return os.path.dirname(self.file_path) if self.file_path else '.'

    def choose_color1(self):
        color = self.color_dialog.getColor(self.line_color, u'Choose line color',
                                           default=DEFAULT_LINE_COLOR)
        if color:
            self.line_color = color
            Shape.line_color = color
            self.canvas.set_drawing_color(color)
            self.canvas.update()
            self.set_dirty()

    def delete_selected_shape(self):
        # Delete all selected shapes
        if self.canvas.selected_shapes:
            # Make a copy of the list since we'll be modifying it
            shapes_to_delete = list(self.canvas.selected_shapes)
            for shape in shapes_to_delete:
                if shape in self.canvas.shapes:
                    self.canvas.shapes.remove(shape)
                    self.remove_label(shape)
            self.canvas.de_select_shape()
        else:
            # Fallback to single deletion
            self.remove_label(self.canvas.delete_selected())

        self.set_dirty()

        # After deletion, select the last visible box if any visible boxes remain
        last_visible_item = self.get_last_visible_label_item()
        if last_visible_item and last_visible_item in self.items_to_shapes:
            last_shape = self.items_to_shapes[last_visible_item]
            self.canvas.select_shape(last_shape)
        elif self.no_shapes():
            for action in self.actions.onShapesPresent:
                action.setEnabled(False)

    def choose_shape_line_color(self):
        color = self.color_dialog.getColor(self.line_color, u'Choose Line Color',
                                           default=DEFAULT_LINE_COLOR)
        if color:
            self.canvas.selected_shape.line_color = color
            self.canvas.update()
            self.set_dirty()

    def choose_shape_fill_color(self):
        color = self.color_dialog.getColor(self.fill_color, u'Choose Fill Color',
                                           default=DEFAULT_FILL_COLOR)
        if color:
            self.canvas.selected_shape.fill_color = color
            self.canvas.update()
            self.set_dirty()

    def open_color_palette(self):
        """Open a dialog to set custom colors for each class."""
        dialog = QDialog(self)
        dialog.setWindowTitle('Color Palette - Set Colors Per Class')
        dialog.setMinimumWidth(400)
        dialog.setMinimumHeight(300)

        layout = QVBoxLayout()

        # Add instruction label
        instruction = QLabel('Click on the color button next to each class to change its color:')
        layout.addWidget(instruction)

        # Create scroll area for class list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)

        # Get all classes from label_hist
        classes = self.label_hist if self.label_hist else []

        # Store color buttons for updates
        color_buttons = {}

        for class_name in classes:
            if not class_name:
                continue

            row_layout = QHBoxLayout()

            # Class label
            class_label = QLabel(class_name)
            class_label.setMinimumWidth(150)
            row_layout.addWidget(class_label)

            # Get current color for this class
            if class_name in self.class_colors:
                current_color = self.class_colors[class_name]
            else:
                # Use the generated color
                current_color = generate_color_by_text(class_name)

            # Color preview button
            color_button = QPushButton()
            color_button.setFixedSize(80, 30)
            color_button.setStyleSheet(f'background-color: {current_color.name()}; border: 1px solid #999;')

            # Connect button to color picker
            def make_color_picker(cls_name, btn):
                def pick_color():
                    current = self.class_colors.get(cls_name, generate_color_by_text(cls_name))
                    color = QColorDialog.getColor(current, dialog, f'Choose color for {cls_name}')
                    if color.isValid():
                        self.class_colors[cls_name] = color
                        btn.setStyleSheet(f'background-color: {color.name()}; border: 1px solid #999;')
                        # Update canvas to reflect new colors
                        self.canvas.update()
                        self.set_dirty()
                return pick_color

            color_button.clicked.connect(make_color_picker(class_name, color_button))
            color_buttons[class_name] = color_button

            row_layout.addWidget(color_button)
            row_layout.addStretch()

            scroll_layout.addLayout(row_layout)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        # Add reset button
        button_layout = QHBoxLayout()
        reset_button = QPushButton('Reset All to Default')
        reset_button.clicked.connect(lambda: self.reset_all_colors(dialog))
        button_layout.addWidget(reset_button)

        close_button = QPushButton('Close')
        close_button.clicked.connect(dialog.accept)
        button_layout.addStretch()
        button_layout.addWidget(close_button)

        layout.addLayout(button_layout)
        dialog.setLayout(layout)
        dialog.exec_()

    def reset_all_colors(self, dialog):
        """Reset all class colors to default."""
        self.class_colors = {}
        self.canvas.update()
        self.set_dirty()
        dialog.accept()
        # Reopen dialog to show updated colors
        self.open_color_palette()

    def copy_shape(self):
        self.canvas.end_move(copy=True)
        self.add_label(self.canvas.selected_shape)
        self.set_dirty()

    def move_shape(self):
        self.canvas.end_move(copy=False)
        self.set_dirty()

    def load_predefined_classes(self, predef_classes_file):
        if os.path.exists(predef_classes_file) is True:
            with codecs.open(predef_classes_file, 'r', 'utf8') as f:
                for line in f:
                    line = line.strip()
                    if not line:  # Skip empty lines
                        continue
                    # Only add if not already in label_hist (prevent duplicates)
                    if self.label_hist is None:
                        self.label_hist = [line]
                    elif line not in self.label_hist:
                        self.label_hist.append(line)
                    # Add to default label dropdown if it exists and not already there
                    if hasattr(self, 'default_label_combo'):
                        # Check if item already exists in combobox
                        if self.default_label_combo.findText(line) == -1:
                            self.default_label_combo.addItem(line)
                    # Add to class visibility list if it exists and not already there
                    if hasattr(self, 'class_visibility_list'):
                        # Check if class already exists in visibility list
                        class_exists = False
                        for i in range(self.class_visibility_list.count()):
                            if self.class_visibility_list.item(i).data(Qt.UserRole) == line:
                                class_exists = True
                                break
                        if not class_exists:
                            # Add new class to visibility list with checkbox
                            new_item = QListWidgetItem(line)
                            new_item.setFlags(new_item.flags() | Qt.ItemIsUserCheckable)
                            new_item.setCheckState(Qt.Unchecked)  # Default unchecked
                            new_item.setData(Qt.UserRole, line)
                            self.class_visibility_list.addItem(new_item)

    def load_pascal_xml_by_filename(self, xml_path):
        if self.file_path is None:
            return
        if os.path.isfile(xml_path) is False:
            return

        self.set_format(FORMAT_PASCALVOC)

        t_voc_parse_reader = PascalVocReader(xml_path)
        shapes = t_voc_parse_reader.get_shapes()
        self.load_labels(shapes)
        self.canvas.verified = t_voc_parse_reader.verified

    def load_yolo_txt_by_filename(self, txt_path):
        if self.file_path is None:
            return
        if os.path.isfile(txt_path) is False:
            return

        self.set_format(FORMAT_YOLO)
        t_yolo_parse_reader = YoloReader(txt_path, self.image)
        shapes = t_yolo_parse_reader.get_shapes()

        print(shapes)
        self.load_labels(shapes)
        self.canvas.verified = t_yolo_parse_reader.verified

        # Check for invalid classes and auto-clean the file
        if t_yolo_parse_reader.invalid_classes:
            total_classes = len(t_yolo_parse_reader.classes)
            invalid_info = []
            total_invalid = 0
            for class_idx, count in t_yolo_parse_reader.invalid_classes.items():
                invalid_info.append(f"{count} object(s) with class index {class_idx}")
                total_invalid += count

            msg = f"Invalid class labels detected and removed!\n\n"
            msg += f"Total classes in classes.txt: {total_classes} (indices 0-{total_classes-1})\n"
            msg += f"Invalid annotations removed:\n"
            msg += "\n".join(invalid_info)
            msg += f"\n\nTotal invalid objects: {total_invalid}"
            msg += f"\n\nThe annotation file has been automatically cleaned."

            # Immediately save to remove invalid annotations from file
            self.save_file()

            QMessageBox.information(self, 'Invalid Classes Removed', msg)

    def load_create_ml_json_by_filename(self, json_path, file_path):
        if self.file_path is None:
            return
        if os.path.isfile(json_path) is False:
            return

        self.set_format(FORMAT_CREATEML)

        create_ml_parse_reader = CreateMLReader(json_path, file_path)
        shapes = create_ml_parse_reader.get_shapes()
        self.load_labels(shapes)
        self.canvas.verified = create_ml_parse_reader.verified

    def copy_previous_bounding_boxes(self):
        current_index = self.m_img_list.index(self.file_path)
        if current_index - 1 >= 0:
            prev_file_path = self.m_img_list[current_index - 1]
            self.show_bounding_box_from_annotation_file(prev_file_path)
            self.save_file()

    def select_all_boxes(self):
        """Select all bounding boxes."""
        self.canvas.select_all_shapes()
        # Enable copy action when shapes are selected
        self.actions.copySelection.setEnabled(True)

    def copy_selected_boxes(self):
        """Copy selected boxes to clipboard."""
        self.clipboard = self.canvas.copy_selected_shapes()
        if self.clipboard:
            # Track which image these boxes were copied from
            self.clipboard_source_image = self.file_path
            self.statusBar().showMessage(f'Copied {len(self.clipboard)} box(es) to clipboard')
            self.statusBar().show()
            # Enable paste action
            self.actions.pasteSelection.setEnabled(True)

    def paste_selected_boxes(self):
        """Paste boxes from clipboard."""
        if not self.clipboard:
            self.statusBar().showMessage('Clipboard is empty')
            self.statusBar().show()
            return

        # Check if pasting to the same image or different image
        is_same_image = (self.clipboard_source_image == self.file_path)

        clipboard_count = len(self.clipboard)
        # Only apply offset when pasting to same image
        # Only check for duplicates when pasting to a different image
        pasted_shapes = self.canvas.paste_shapes(
            self.clipboard,
            check_duplicates=not is_same_image,
            apply_offset=is_same_image
        )

        for shape in pasted_shapes:
            self.add_label(shape)

        # Update clipboard with newly pasted positions for repeated same-image pastes
        if is_same_image and pasted_shapes:
            self.clipboard = pasted_shapes

        skipped_count = clipboard_count - len(pasted_shapes)
        self.set_dirty()

        if skipped_count > 0:
            self.statusBar().showMessage(f'Pasted {len(pasted_shapes)} box(es), skipped {skipped_count} duplicate(s)')
        else:
            self.statusBar().showMessage(f'Pasted {len(pasted_shapes)} box(es)')
        self.statusBar().show()

    def toggle_paint_labels_option(self):
        for shape in self.canvas.shapes:
            shape.paint_label = self.display_label_option.isChecked()

    def toggle_draw_square(self):
        self.canvas.set_drawing_shape_to_square(self.draw_squares_option.isChecked())

def inverted(color):
    return QColor(*[255 - v for v in color.getRgb()])


def read(filename, default=None):
    try:
        reader = QImageReader(filename)
        reader.setAutoTransform(True)
        return reader.read()
    except:
        return default


def get_main_app(argv=[]):
    """
    Standard boilerplate Qt application code.
    Do everything but app.exec_() -- so that we can test the application in one thread
    """
    app = QApplication(argv)
    app.setApplicationName(__appname__)
    app.setWindowIcon(new_icon("app"))
    # Tzutalin 201705+: Accept extra agruments to change predefined class file
    argparser = argparse.ArgumentParser()
    argparser.add_argument("image_dir", nargs="?")
    default_class_file = os.path.join(os.path.dirname(__file__), "data", "predefined_classes.txt")
    argparser.add_argument("class_file",
                           default=default_class_file,
                           nargs="?")
    argparser.add_argument("save_dir", nargs="?")
    args = argparser.parse_args(argv[1:])

    args.image_dir = args.image_dir and os.path.normpath(args.image_dir)

    # If classes file is the default and image_dir is provided, look for classes in image_dir
    if args.image_dir and args.class_file == default_class_file:
        # Try common class file names in image_dir
        possible_class_files = [
            os.path.join(args.image_dir, "classes.txt"),
            os.path.join(args.image_dir, "predefined_classes.txt")
        ]
        for class_file in possible_class_files:
            if os.path.exists(class_file):
                args.class_file = class_file
                break

    args.class_file = args.class_file and os.path.normpath(args.class_file)
    args.save_dir = args.save_dir and os.path.normpath(args.save_dir)

    # If save_dir not provided, default to image_dir
    if args.image_dir and not args.save_dir:
        args.save_dir = args.image_dir

    # Usage : labelImg.py image classFile saveDir
    win = MainWindow(args.image_dir,
                     args.class_file,
                     args.save_dir)
    win.show()
    return app, win


def auto_update_from_git():
    """Pull latest changes from git before starting the application"""
    import tempfile
    try:
        print("Checking for updates...")

        # Get the site-packages directory
        site_packages = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        temp_dir = tempfile.mkdtemp()
        repo_url = "https://github.com/retrocausal-ai/acp-label-img.git"

        # Clone the repo to temp directory (10 second timeout)
        result = subprocess.run(
            ['git', 'clone', '--depth', '1', repo_url, temp_dir],
            capture_output=True, text=True, timeout=10
        )

        if result.returncode == 0:
            # Copy updated files to site-packages
            labelimg_src = os.path.join(temp_dir, 'labelImg')
            libs_src = os.path.join(temp_dir, 'libs')
            labelimg_dst = os.path.join(site_packages, 'labelImg')
            libs_dst = os.path.join(site_packages, 'libs')

            # Update labelImg
            for file in os.listdir(labelimg_src):
                if file != '__pycache__':
                    src_file = os.path.join(labelimg_src, file)
                    dst_file = os.path.join(labelimg_dst, file)
                    if os.path.isfile(src_file):
                        shutil.copy2(src_file, dst_file)

            # Update libs
            for file in os.listdir(libs_src):
                if file != '__pycache__':
                    src_file = os.path.join(libs_src, file)
                    dst_file = os.path.join(libs_dst, file)
                    if os.path.isfile(src_file):
                        shutil.copy2(src_file, dst_file)

            print("✓ Updated to latest version")

            # Clean up
            shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            print("Already up to date")

        print("Starting labelImg...\n")

    except subprocess.TimeoutExpired:
        print("Update check timed out, starting application anyway...")
    except FileNotFoundError:
        print("Git not found, skipping auto-update...")
    except Exception as e:
        print(f"Update check failed: {e}, starting application anyway...")

def main():
    """construct main app and run it"""
    # Auto-update from git before starting
    auto_update_from_git()

    app, _win = get_main_app(sys.argv)
    return app.exec_()

if __name__ == '__main__':
    sys.exit(main())
