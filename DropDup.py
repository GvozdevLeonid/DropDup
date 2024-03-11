from ImageHash import (
    hex_to_multihash,
    hex_to_hash,

    crop_resistant_hash,
    ahash,
    dhash,
    phash,
    rhash,
)
from PySide6 import (
    QtWidgets,
    QtCore,
    QtGui,
)
from concurrent.futures import (
    ProcessPoolExecutor,
    as_completed
)
from application_path import application_path
from multiprocessing import freeze_support
from PIL import Image
import subprocess
import shutil
import peewee
import sys
import os

database = peewee.SqliteDatabase(os.path.join(application_path(), 'processing.sqlite3'))
algorithms = {
    'rhash': rhash,
    'phash': phash,
    'ahash': ahash,
    'dhash': dhash,
}

title_font = QtGui.QFont('OpenSans', 18)
text_font = QtGui.QFont('OpenSans', 14)
settings = QtCore.QSettings('DropDup', 'settings')


def remove_groups(groups):
    processed_images = {}
    for group in groups:
        sorted_group = sorted(group, key=lambda processed_image: processed_image['image_width'] * processed_image['image_height'] * processed_image['image_dpi'])
        processed_images = processed_images.union([processed_image['image_path']] for processed_image in sorted_group)
        for other_processed_image in sorted_group:
            os.remove(other_processed_image['image_path'])

    return processed_images


def move_groups(groups, path_to_duplicates):
    processed_images = {}
    for group in groups:
        sorted_group = sorted(group, key=lambda processed_image: processed_image['image_width'] * processed_image['image_height'] * processed_image['image_dpi'])
        processed_images = processed_images.union([processed_image['image_path']] for processed_image in sorted_group)
        for other_processed_image in sorted_group:
            image_path = other_processed_image['image_path']
            shutil.move(image_path, os.path.join(path_to_duplicates, os.path.split(image_path)[1]))

    return processed_images


def remove_files(files):
    for filepath in files:
        os.remove(filepath)


def move_files(files, path_to_duplicates):
    for filepath in files:
        shutil.move(filepath, os.path.join(path_to_duplicates, os.path.split(filepath)[1]))


def update_groups(groups, processed_images):
    new_groups = []

    for group in groups:
        new_groups.append([processed_image for processed_image in group if processed_image['image_path'] not in processed_images])

    return new_groups


def open_file_explorer(path):
    if os.path.isfile(path):
        directory = os.path.dirname(path)
    else:
        directory = path

    if sys.platform == 'win32':
        subprocess.run(['explorer', '/select,', os.path.normpath(path)])
    elif sys.platform == 'darwin':
        subprocess.run(['open', '-R', path])
    elif sys.platform.startswith('linux'):
        subprocess.run(['xdg-open', directory])


def _create_hash(filepath, algorithm, algorithm_str, hash_size, use_crop_resistant_hash):
    image = Image.open(filepath)
    image_hash = ''

    kwargs = {
        'hash_size': hash_size
    }
    if algorithm_str == 'rhash':
        kwargs['block_size'] = hash_size * 2
    elif algorithm_str == 'phash':
        kwargs['highfreq_factor'] = hash_size * 2

    if use_crop_resistant_hash:
        image_hash = crop_resistant_hash(image, algorithm, **kwargs)
    else:
        image_hash = algorithm(image, **kwargs)

    image_width, image_height = image.size
    image_dpi = 72
    if 'dpi' in image.info:
        image_dpi = int(max(image.info['dpi']))
    image_size = os.path.getsize(filepath) / 1048576

    return {
        'image_hash': str(image_hash),
        'image_path': filepath,
        'image_width': image_width,
        'image_height': image_height,
        'image_dpi': image_dpi,
        'image_size': image_size,
    }


class ProcessedImage(peewee.Model):
    class Meta:
        database = database

    id = peewee.IntegerField(primary_key=True)
    image_path = peewee.TextField()
    image_hash = peewee.TextField()
    image_width = peewee.IntegerField()
    image_height = peewee.IntegerField()
    image_dpi = peewee.IntegerField()
    image_size = peewee.FloatField()


class FolderNameValidator(QtGui.QValidator):
    def __init__(self):
        QtGui.QValidator.__init__(self)
        self.invalidChars = QtCore.QRegularExpression(r"[<>:\"/\\|\?\*\x00-\x1f]")

    def validate(self, input, pos):
        match = self.invalidChars.match(input)
        if match.hasMatch() or input.endswith('.') or input.endswith(' ') or input == '':
            return (QtGui.QValidator.Invalid, input, pos)
        return (QtGui.QValidator.Acceptable, input, pos)


class FindDuplicatesThread(QtCore.QThread):
    process_signal = QtCore.Signal(float)

    def __init__(self, path):
        QtCore.QThread.__init__(self)
        self._path = path
        self._processed_images = 0
        self._progress = 0

        self.duplicates = []
        self.full_duplicates = []

    def run(self):
        self.process_signal.emit(self._progress)

        with database:
            database.create_tables([ProcessedImage])
        ProcessedImage.delete().execute()

        self.__create_images_hash()
        self.duplicates = self.__find_duplicates()
        self.full_duplicates = self.__find_full_duplicates()

        sorting_mode = settings.value('sorting_mode', 'h-l', str)

        if sorting_mode == 'h-l':
            self.duplicates = sorted(self.duplicates, key=self.__calculate_average_difference)
        elif sorting_mode == 'l-h':
            self.duplicates = sorted(self.duplicates, key=self.__calculate_average_difference, reverse=True)

        self.process_signal.emit(100)

    def __calculate_average_difference(self, group):
        differences = []
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                differences.append(self.__get_hash(group[i]['image_hash']) - self.__get_hash(group[j]['image_hash']))

        return sum(differences) / len(differences)

    def __is_image(self, filename):
        _, ext = os.path.splitext(filename)
        if ext.lower() in ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.svg'):
            return True
        return False

    def __get_hash(self, hex_hash):
        return hex_to_hash(hex_hash) if ',' not in hex_hash else hex_to_multihash(hex_hash)

    def __create_images_hash(self, max_progress: int = 60):
        algorithm_str = settings.value('algorithm', 'rhash', str)
        algorithm = algorithms[algorithm_str]
        use_crop_resistant_hash = settings.value('use_crop_resistant_hash', False, bool)
        hash_size = settings.value('hash_size', 8, int)

        listdir = []
        if settings.value('check_subdirectories', False, bool):
            listdir = [os.path.join(root, name) for root, dirs, files in os.walk(self._path) for name in files if self.__is_image(os.path.join(root, name))]
        else:
            listdir = [os.path.join(self._path, name) for name in os.listdir(self._path) if self.__is_image(os.path.join(self._path, name))]

        iterations = len(listdir)
        if iterations > 1:
            step = round(max_progress / iterations, 2)
            with ProcessPoolExecutor() as executor:
                results = [executor.submit(_create_hash, filepath, algorithm, algorithm_str, hash_size, use_crop_resistant_hash) for filepath in listdir]
                for result in as_completed(results):
                    processed_image_data = result.result()

                    processed_image = ProcessedImage.create(**processed_image_data)
                    processed_image.save()
                    self._processed_images += 1
                    self._progress += step
                    self.process_signal.emit(self._progress)
        else:
            self._progress += max_progress
            self.process_signal.emit(self._progress)

    def __find_duplicates(self, max_progress: int = 20):
        if self._processed_images:
            step = round(max_progress / ((self._processed_images - 1) * self._processed_images // 2), 2)
            threshold = settings.value('duplicate_threshold', 97.0, float)
            duplicates = {}

            for current_image_idx in range(self._processed_images):
                current_image = ProcessedImage.select().where(ProcessedImage.id == current_image_idx + 1).dicts().get()
                current_hash = self.__get_hash(current_image['image_hash'])

                for other_image_idx in range(current_image_idx + 1, self._processed_images):
                    other_image = ProcessedImage.select().where(ProcessedImage.id == other_image_idx + 1).dicts().get()
                    other_hash = self.__get_hash(other_image['image_hash'])

                    difference = current_hash - other_hash

                    if (1 - difference) >= round(threshold / 100, 2):
                        duplicates[current_image_idx] = duplicates.get(current_image_idx, []) + [current_image, other_image]

                    self._progress += step
                    self.process_signal.emit(self._progress)

            return self.__group_duplicates(list(duplicates.values()))

        self._progress += max_progress
        self.process_signal.emit(self._progress)

        return self.__group_duplicates([])

    def __find_full_duplicates(self, max_progress: int = 10):
        full_duplicates = {}
        subquery_hash = (ProcessedImage
                         .select(ProcessedImage.image_hash)
                         .group_by(ProcessedImage.image_hash)
                         .having(peewee.fn.COUNT(ProcessedImage.image_hash) > 1))

        duplicates = list(ProcessedImage.select().where(ProcessedImage.image_hash.in_(subquery_hash)).dicts())
        if len(duplicates):
            step = round(max_progress / len(duplicates), 2)

            for image in duplicates:
                full_duplicates[image['image_hash']] = full_duplicates.get(image['image_hash'], []) + [image]

                self._progress += step
                self.process_signal.emit(self._progress)

            return self.__group_duplicates(list(full_duplicates.values()))

        self._progress += max_progress
        self.process_signal.emit(self._progress)

        return self.__group_duplicates([])

    def __group_duplicates(self, duplicates, max_progress: int = 5):
        if len(duplicates):
            step = round(max_progress / len(duplicates), 2)
            groups = {}

            for duplicate_values in duplicates:
                duplicate_key = set([image['image_path'] for image in duplicate_values])
                to_union = []
                for key in groups.keys():
                    if not duplicate_key.isdisjoint(set(key)):
                        to_union.append(key)

                if len(to_union):
                    new_key = duplicate_key
                    group = duplicate_values

                    for key in to_union:
                        new_key = new_key.union(key)
                        old_group = groups.pop(key)
                        for duplicate in old_group:
                            if duplicate not in group:
                                group.append(duplicate)

                    groups[tuple(new_key)] = group

                else:
                    groups[tuple(duplicate_key)] = duplicate_values

                self._progress += step
                self.process_signal.emit(self._progress)

            return list(groups.values())

        self._progress += max_progress
        self.process_signal.emit(self._progress)

        return []


class PreviewProcessedImage(QtWidgets.QPushButton):
    def __init__(self, processed_image):
        QtWidgets.QPushButton.__init__(self)
        self.processed_image = processed_image
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Minimum)

        self.setToolTip(self.processed_image['image_path'])
        self.setToolTipDuration(5000)
        image_preview_size = settings.value('image_preview_size', 150, int)

        layout = QtWidgets.QGridLayout()
        pixmap = QtGui.QPixmap(self.processed_image['image_path'])
        pixmap = pixmap.scaled(QtCore.QSize(image_preview_size, image_preview_size), QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation)
        self.image = QtWidgets.QLabel()
        self.image.resize(image_preview_size, image_preview_size)
        self.image.setScaledContents(True)
        self.image.setPixmap(pixmap)
        self.filname = QtWidgets.QLabel(font=title_font)
        self.filname.adjustSize()
        self.resolution = QtWidgets.QLabel(font=text_font)
        self.dpi = QtWidgets.QLabel(font=text_font)
        self.image_size = QtWidgets.QLabel(font=text_font)
        self.selected = QtWidgets.QCheckBox()

        copy_path_button = QtWidgets.QPushButton()
        copy_path_button_icon = self.style().standardIcon(QtWidgets.QStyle.SP_DialogSaveButton)
        copy_path_button.setIcon(copy_path_button_icon)
        copy_path_button.clicked.connect(lambda: QtGui.QClipboard().setText(self.processed_image['image_path']))

        open_path_button = QtWidgets.QPushButton()
        open_path_button_icon = self.style().standardIcon(QtWidgets.QStyle.SP_DirIcon)
        open_path_button.setIcon(open_path_button_icon)
        open_path_button.clicked.connect(lambda: open_file_explorer(self.processed_image['image_path']))

        if settings.value('show_filename', True, bool):
            self.filname.setText(os.path.split(self.processed_image['image_path'])[1])

        if settings.value('show_file_size', False, bool):
            self.image_size.setText(f'Size {round(self.processed_image["image_size"], 2)} MB')

        if settings.value('show_additional_info', False, bool):
            self.resolution.setText(f'Resolution {self.processed_image["image_width"]} * {self.processed_image["image_height"]}')
            self.dpi.setText(f'DPI {self.processed_image["image_dpi"]}')

        layout.addWidget(self.selected, 0, 0)
        layout.addWidget(self.filname, 0, 1, 1, 2)
        layout.addWidget(self.image, 1, 0, 3, 3, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.resolution, 4, 0, 1, 3)
        layout.addWidget(self.dpi, 5, 0)
        layout.addWidget(copy_path_button, 5, 1)
        layout.addWidget(open_path_button, 5, 2)
        layout.addWidget(self.image_size, 6, 0, 1, 3)

        self.setLayout(layout)
        self.setMinimumSize(image_preview_size + 20, image_preview_size + 60)

        self.pressed.connect(self._select)

    def _select(self):
        self.selected.setChecked(not self.selected.isChecked())

    def isChecked(self):
        return self.selected.isChecked()


class ProcessPage(QtWidgets.QWidget):
    signal = QtCore.Signal(dict)

    def __init__(self) -> None:
        QtWidgets.QWidget.__init__(self)

        layout = QtWidgets.QGridLayout()

        folder_path_layout = QtWidgets.QGridLayout()
        folder_path_label = QtWidgets.QLabel('Folder path', font=title_font)
        self.folder_path = QtWidgets.QLineEdit(font=text_font)
        self.folder_path.setReadOnly(True)
        folder_path_change_button = QtWidgets.QPushButton('View', font=text_font)
        folder_path_change_button.clicked.connect(self.select_path)

        folder_path_layout.addWidget(folder_path_label, 0, 0)
        folder_path_layout.addWidget(self.folder_path, 1, 0)
        folder_path_layout.addWidget(folder_path_change_button, 1, 1)
        self.progress = QtWidgets.QProgressBar(value=0, font=text_font)

        self.button_start = QtWidgets.QPushButton('Process', font=text_font)
        self.button_start.clicked.connect(self.start_processing)

        layout.addLayout(folder_path_layout, 0, 0, 2, 2)
        layout.addWidget(self.progress, 2, 0, 1, 2)
        layout.addWidget(self.button_start, 3, 0, 1, 2, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)

        layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(20)

        self.setLayout(layout)

        self.duplicates = []

    def select_path(self):
        self.folder_path.setText(QtWidgets.QFileDialog.getExistingDirectory(self, 'Select directory with images'))

    def start_processing(self):
        if os.path.exists(self.folder_path.text()):
            self.button_start.setDisabled(True)
            self.find_duplicates_thread = FindDuplicatesThread(self.folder_path.text())
            self.find_duplicates_thread.process_signal.connect(self.change_progress)
            self.find_duplicates_thread.start()

    def change_progress(self, value):
        self.progress.setValue(value)
        if value == 100:
            duplicates, full_duplicates = self.find_duplicates_thread.duplicates, self.find_duplicates_thread.full_duplicates
            self.find_duplicates_thread = None
            self.parent()._pre_process_duplicates(self.folder_path.text(), duplicates, full_duplicates)

            self.button_start.setDisabled(False)


class ResultPage(QtWidgets.QWidget):
    signal = QtCore.Signal(dict)

    def __init__(self, folder_path, duplicates) -> None:
        QtWidgets.QWidget.__init__(self)
        self.folder_path = folder_path
        self.duplicates = []

        preview_layout = QtWidgets.QGridLayout()
        preview_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)

        scroll_widget = QtWidgets.QWidget()
        scroll_widget.setLayout(preview_layout)
        scroll_widget.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                    QtWidgets.QSizePolicy.Policy.Expanding)

        scroll = QtWidgets.QScrollArea()
        scroll.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                             QtWidgets.QSizePolicy.Policy.Expanding)
        scroll.setWidget(scroll_widget)
        scroll.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        scroll.setWidgetResizable(True)

        label = QtWidgets.QLabel('Select originals', font=title_font)
        buttons_layout = QtWidgets.QHBoxLayout()
        button_cancel = QtWidgets.QPushButton('Cancel', font=text_font)
        button_cancel.clicked.connect(lambda _: self.signal.emit(True))
        button_continue = QtWidgets.QPushButton('Continue', font=text_font)
        button_continue.clicked.connect(self._continue)
        buttons_layout.addWidget(button_cancel)
        buttons_layout.addWidget(button_continue)

        layout = QtWidgets.QGridLayout()
        layout.setRowStretch(1, 1)
        layout.addWidget(label, 0, 0, 1, 4, alignment=QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(scroll, 1, 0, 9, 4)
        layout.addLayout(buttons_layout, 10, 3)
        self.setLayout(layout)

        for i, duplicate_values in enumerate(duplicates):
            for j, duplicate in enumerate(duplicate_values):
                duplicate = PreviewProcessedImage(duplicate)
                self.duplicates.append(duplicate)
                preview_layout.addWidget(
                    duplicate, i, j
                )

    def _continue(self):
        duplicate_files = []
        for duplicate in self.duplicates:
            if not duplicate.isChecked():
                duplicate_files.append(duplicate.processed_image['image_path'])

        self.parent()._process_duplicates(self.folder_path, duplicate_files)
        self.signal.emit(True)


class SettingsPage(QtWidgets.QWidget):
    signal = QtCore.Signal(dict)

    def __init__(self) -> None:
        QtWidgets.QWidget.__init__(self)

        layout = QtWidgets.QGridLayout()

        duplicate_folder_name_layout = QtWidgets.QVBoxLayout()
        duplicate_folder_name_layout.setSpacing(5)
        duplicate_folder_name_label = QtWidgets.QLabel("Duplicate folder name", font=title_font)
        self.duplicate_folder_name = QtWidgets.QLineEdit(font=text_font, text=settings.value('duplicate_folder_name', 'Duplicates', str))
        self.duplicate_folder_name.setValidator(FolderNameValidator())
        duplicate_folder_name_layout.addWidget(duplicate_folder_name_label)
        duplicate_folder_name_layout.addWidget(self.duplicate_folder_name)

        duplicate_threshold_layout = QtWidgets.QVBoxLayout()
        duplicate_threshold_layout.setSpacing(5)
        self.duplicate_threshold = QtWidgets.QDoubleSpinBox(minimum=10, maximum=100, value=settings.value('duplicate_threshold', 97.0, float), font=text_font)
        duplicate_threshold_label = QtWidgets.QLabel("Duplicate threshold", font=title_font)
        duplicate_threshold_layout.addWidget(duplicate_threshold_label)
        duplicate_threshold_layout.addWidget(self.duplicate_threshold)

        hash_size_layout = QtWidgets.QVBoxLayout()
        hash_size_layout.setSpacing(5)
        self.hash_size = QtWidgets.QComboBox(font=text_font)
        self.hash_size.addItems([str(2 ** i) for i in (3, 4, 5, 6, 7)])
        self.hash_size.setCurrentText(str(settings.value('hash_size', 8, int)))
        hash_size_label = QtWidgets.QLabel("Hash size", font=title_font)
        hash_size_layout.addWidget(hash_size_label)
        hash_size_layout.addWidget(self.hash_size)

        self.check_subdirectories = QtWidgets.QRadioButton(font=text_font, text="Check subdirectories", checked=settings.value('check_subdirectories', False, bool))

        self.algorithm = QtWidgets.QComboBox(font=text_font)
        self.algorithm.addItems(['rhash', 'phash', 'ahash', 'dhash'])
        self.algorithm.setCurrentText(settings.value('algorithm', 'rhash', str))

        self.use_crop_resistant_hash = QtWidgets.QRadioButton(font=text_font, text="Use crop resistant hash", checked=settings.value('use_crop_resistant_hash', False, bool))

        action_mode_value = settings.value('action_mode', 'manual', str)
        action_mode_group_layout = QtWidgets.QVBoxLayout()
        action_mode_group = QtWidgets.QGroupBox(font=title_font, title="Action mode")
        action_mode_group.setLayout(action_mode_group_layout)
        self.action_mode_auto = QtWidgets.QRadioButton(font=text_font, text='Auto')
        self.action_mode_semi_auto = QtWidgets.QRadioButton(font=text_font, text='Semi auto')
        self.action_mode_manual = QtWidgets.QRadioButton(font=text_font, text='Manual')
        action_mode_group_layout.addWidget(self.action_mode_auto)
        action_mode_group_layout.addWidget(self.action_mode_semi_auto)
        action_mode_group_layout.addWidget(self.action_mode_manual)

        if action_mode_value == 'manual':
            self.action_mode_manual.setChecked(True)
        elif action_mode_value == 'semi-auto':
            self.action_mode_semi_auto.setChecked(True)
        elif action_mode_value == 'auto':
            self.action_mode_auto.setChecked(True)

        duplicates_action_value = settings.value('duplicates_action', 'move', str)
        duplicates_action_group_layout = QtWidgets.QVBoxLayout()
        duplicates_action_group = QtWidgets.QGroupBox(font=title_font, title='Action for duplicates')
        duplicates_action_group.setLayout(duplicates_action_group_layout)
        self.duplicates_action_delete = QtWidgets.QRadioButton(font=text_font, text='Delete')
        self.duplicates_action_move = QtWidgets.QRadioButton(font=text_font, text='Move to folder')
        duplicates_action_group_layout.addWidget(self.duplicates_action_delete)
        duplicates_action_group_layout.addWidget(self.duplicates_action_move)

        if duplicates_action_value == 'move':
            self.duplicates_action_move.setChecked(True)
        elif duplicates_action_value == 'delete':
            self.duplicates_action_delete.setChecked(True)

        sorting_mode_value = settings.value('sorting_mode', 'h-l', str)
        sorting_mode_group_layout = QtWidgets.QVBoxLayout()
        sorting_mode_group = QtWidgets.QGroupBox(font=title_font, title="Sorting mode")
        sorting_mode_group.setLayout(sorting_mode_group_layout)
        self.sorting_mode_high_to_low = QtWidgets.QRadioButton(font=text_font, text='Hight to low')
        self.sorting_mode_low_to_high = QtWidgets.QRadioButton(font=text_font, text='Low to high')
        sorting_mode_group_layout.addWidget(self.sorting_mode_high_to_low)
        sorting_mode_group_layout.addWidget(self.sorting_mode_low_to_high)

        if sorting_mode_value == 'h-l':
            self.sorting_mode_high_to_low.setChecked(True)
        elif sorting_mode_value == 'l-h':
            self.sorting_mode_low_to_high.setChecked(True)

        view_settings_group_layout = QtWidgets.QVBoxLayout()
        view_settings_group = QtWidgets.QGroupBox(font=title_font, title="View settings")
        view_settings_group.setLayout(view_settings_group_layout)
        self.view_settings_show_filename = QtWidgets.QCheckBox(font=text_font, text='Show filename', checked=settings.value('show_filename', True, bool))
        self.view_settings_show_file_size = QtWidgets.QCheckBox(font=text_font, text='Show file size', checked=settings.value('show_file_size', False, bool))
        self.view_settings_show_additional_info = QtWidgets.QCheckBox(font=text_font, text='Show additional info (resolution, dpi)', checked=settings.value('show_additional_info', False, bool))
        view_settings_group_layout.addWidget(self.view_settings_show_filename)
        view_settings_group_layout.addWidget(self.view_settings_show_file_size)
        view_settings_group_layout.addWidget(self.view_settings_show_additional_info)

        image_preview_size_layout = QtWidgets.QVBoxLayout()
        image_preview_size_layout.setSpacing(5)
        self.image_preview_size = QtWidgets.QSpinBox(minimum=100, maximum=400,value=settings.value('image_preview_size', 150, int), font=text_font)
        image_preview_size_label = QtWidgets.QLabel("Image preview size", font=title_font)
        self.image_preview_size.setSingleStep(10)
        image_preview_size_layout.addWidget(image_preview_size_label)
        image_preview_size_layout.addWidget(self.image_preview_size)

        buttons_layout = QtWidgets.QHBoxLayout()
        button_cancel = QtWidgets.QPushButton('Cancel', font=text_font)
        button_cancel.clicked.connect(lambda _: self.signal.emit(True))
        button_save = QtWidgets.QPushButton('Save', font=text_font)
        button_save.clicked.connect(self._save)
        buttons_layout.addWidget(button_cancel)
        buttons_layout.addWidget(button_save)

        layout.addLayout(duplicate_folder_name_layout, 0, 0, 2, 2)
        layout.addLayout(duplicate_threshold_layout, 0, 2, 2, 2)
        layout.addLayout(hash_size_layout, 0, 4, 2, 2)
        layout.addWidget(self.check_subdirectories, 2, 0, 1, 2)
        layout.addWidget(self.algorithm, 2, 2, 1, 2)
        layout.addWidget(self.use_crop_resistant_hash, 2, 4, 1, 2)
        layout.addWidget(action_mode_group, 3, 0, 2, 3)
        layout.addWidget(duplicates_action_group, 3, 3, 2, 3)
        layout.addWidget(sorting_mode_group, 5, 0, 2, 6)
        layout.addWidget(view_settings_group, 7, 0, 3, 3)
        layout.addLayout(image_preview_size_layout, 7, 3, 2, 3)
        layout.addLayout(buttons_layout, 10, 4, 1, 2)

        layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(20)

        self.setLayout(layout)

    def _save(self):
        action_mode_value = 'manual'
        duplicates_action_value = 'move'
        sorting_mode_value = 'h-l'

        if self.action_mode_manual.isChecked():
            action_mode_value = 'manual'
        elif self.action_mode_semi_auto.isChecked():
            action_mode_value = 'semi-auto'
        elif self.action_mode_auto.isChecked():
            action_mode_value = 'auto'

        if self.duplicates_action_move.isChecked():
            duplicates_action_value = 'move'
        elif self.duplicates_action_delete.isChecked():
            duplicates_action_value = 'delete'

        if self.sorting_mode_high_to_low.isChecked():
            sorting_mode_value = 'h-l'
        elif self.sorting_mode_low_to_high.isChecked():
            sorting_mode_value = 'l-h'

        settings.setValue('duplicate_folder_name', self.duplicate_folder_name.text())
        settings.setValue('duplicate_threshold', self.duplicate_threshold.value())
        settings.setValue('hash_size', int(self.hash_size.currentText()))
        settings.setValue('check_subdirectories', self.check_subdirectories.isChecked())
        settings.setValue('use_crop_resistant_hash', self.use_crop_resistant_hash.isChecked())
        settings.setValue('algorithm', self.algorithm.currentText())
        settings.setValue('action_mode', action_mode_value)
        settings.setValue('duplicates_action', duplicates_action_value)
        settings.setValue('sorting_mode', sorting_mode_value)
        settings.setValue('show_filename', self.view_settings_show_filename.isChecked())
        settings.setValue('show_file_size', self.view_settings_show_file_size.isChecked())
        settings.setValue('show_additional_info', self.view_settings_show_additional_info.isChecked())
        settings.setValue('image_preview_size', self.image_preview_size.value())

        self.signal.emit(True)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        QtWidgets.QMainWindow.__init__(self)

        self.setWindowTitle('DropDup')
        self.setWindowIcon(QtGui.QPixmap(os.path.join(application_path(), 'logo.png')))
        self._create_menu()

        self._process_page = ProcessPage()
        self.set_page('process_page')

    def _create_menu(self):
        self.action_open_folder = QtGui.QAction('Open folder')
        self.action_open_settings = QtGui.QAction('App settings')
        self.action_exit = QtGui.QAction('Exit')

        self.action_open_folder.triggered.connect(lambda _: self._process_page.select_path())
        self.action_open_settings.triggered.connect(lambda _: self.set_page('settings_page'))
        self.action_exit.triggered.connect(lambda _: self.close())

        self.file_menu = QtWidgets.QMenu()
        self.file_menu.setTitle('File')
        self.file_menu.addActions([self.action_open_folder, self.action_open_settings])
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.action_exit)

        self.menu_bar = QtWidgets.QMenuBar()
        self.menu_bar.addMenu(self.file_menu)

        self.setMenuBar(self.menu_bar)

    def set_page(self, page_name, **kwargs):
        if isinstance(self.centralWidget(), ProcessPage):
            self._process_page = self.takeCentralWidget()

        match page_name:
            case 'process_page':
                self.setCentralWidget(self._process_page)
                self.setFixedSize(600, 220)

            case 'settings_page':
                settings_page = SettingsPage()
                settings_page.signal.connect(lambda _: self.set_page('process_page'))
                self.setCentralWidget(settings_page)
                self.setFixedSize(740, 660)

            case 'result_page':
                result_page = ResultPage(**kwargs)
                result_page.signal.connect(lambda _: self.set_page('process_page'))
                self.setCentralWidget(result_page)
                self.adjustSize()
                self.setMinimumSize(self.size())
                self.setMaximumSize(QtWidgets.QApplication.primaryScreen().availableSize())
                self.resize(600, 400)
                self.showNormal()

    def _pre_process_duplicates(self, folder_path, duplicates, full_duplicates):
        duplicates_action = settings.value('duplicates_action', 'move', str)
        action_mode = settings.value('action_mode', 'manual', str)
        if action_mode in ('semi-auto', 'auto'):
            if duplicates_action == 'move':
                path_to_duplicates = os.path.join(folder_path, settings.value('duplicate_folder_name', 'Duplicates', str))
                os.makedirs(path_to_duplicates, exist_ok=True)

                if action_mode == 'semi-auto':
                    processed_images = move_groups(full_duplicates, path_to_duplicates)
                    duplicates = update_groups(duplicates, processed_images)
                elif action_mode == 'auto':
                    move_groups(duplicates, path_to_duplicates)

            elif duplicates_action == 'delete':
                if action_mode == 'semi-auto':
                    processed_images = remove_groups(full_duplicates)
                    duplicates = update_groups(duplicates, processed_images)

                elif action_mode == 'auto':
                    remove_groups(duplicates)

        if action_mode in ('manual', 'semi-auto'):
            self.set_page('result_page', folder_path=folder_path, duplicates=duplicates)

    def _process_duplicates(self, folder_path, duplicates):
        duplicates_action = settings.value('duplicates_action', 'move', str)
        if duplicates_action == 'move':
            path_to_duplicates = os.path.join(folder_path, settings.value('duplicate_folder_name', 'Duplicates', str))
            os.makedirs(path_to_duplicates, exist_ok=True)
            move_files(duplicates, path_to_duplicates)

        elif duplicates_action == 'delete':
            remove_files(duplicates)


if __name__ == '__main__':
    freeze_support()
    app = QtWidgets.QApplication()
    main_window = MainWindow()
    main_window.show()
    app.setStyle('Fusion')
    app.exec()

    if os.path.exists(os.path.join(application_path(), 'processing.sqlite3')):
        try:
            os.remove(os.path.join(application_path(), 'processing.sqlite3'))
        except Exception:
            pass
