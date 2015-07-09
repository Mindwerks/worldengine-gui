#!/usr/bin/python
"""
PyQt5 GUI Interface for Worldengine
"""
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QApplication, QDialog, QMainWindow, QAction, \
    QFileDialog, QLabel, QWidget, QGridLayout, QPushButton, QLineEdit, QSpinBox
import platec
import random
import sys
import threading
from worldengine.world import World, Step
from worldengine.common import array_to_matrix
from worldengine.generation import ErosionSimulation
from view import draw_bw_elevation_on_screen, draw_land_on_screen, \
    draw_plates_and_elevation_on_screen, draw_plates_on_screen
from worldengine.plates import add_noise_to_elevation, center_land, \
    initialize_ocean_and_thresholds, place_oceans_at_map_borders
from worldengine.simulations.hydrology import WatermapSimulation
from worldengine.simulations.irrigation import IrrigationSimulation
from worldengine.simulations.humidity import HumiditySimulation
from worldengine.simulations.temperature import TemperatureSimulation
from worldengine.simulations.permeability import PermeabilitySimulation
from worldengine.simulations.biome import BiomeSimulation
from worldengine.simulations.precipitation import PrecipitationSimulation
from views.PrecipitationsView import PrecipitationsView
from views.WatermapView import WatermapView


class GenerateDialog(QDialog):
    def __init__(self, parent):
        QDialog.__init__(self, parent)
        self._init_ui()

    def _init_ui(self):
        self.resize(500, 250)
        self.setWindowTitle('Generate a new world')
        grid = QGridLayout()

        seed = random.randint(0, 65535)

        name_label = QLabel('Name')
        grid.addWidget(name_label, 0, 0, 1, 1)
        name = 'world_seed_%i' % seed
        self.name_value = QLineEdit(name)
        grid.addWidget(self.name_value, 0, 1, 1, 2)

        seed_label = QLabel('Seed')
        grid.addWidget(seed_label, 1, 0, 1, 1)
        self.seed_value = self._spinner_box(0, 65525, seed)
        grid.addWidget(self.seed_value, 1, 1, 1, 2)

        width_label = QLabel('Width')
        grid.addWidget(width_label, 2, 0, 1, 1)
        self.width_value = self._spinner_box(100, 8192, 512)
        grid.addWidget(self.width_value, 2, 1, 1, 2)

        height_label = QLabel('Height')
        grid.addWidget(height_label, 3, 0, 1, 1)
        self.height_value = self._spinner_box(100, 8192, 512)
        grid.addWidget(self.height_value, 3, 1, 1, 2)

        plates_num_label = QLabel('Number of plates')
        grid.addWidget(plates_num_label, 4, 0, 1, 1)
        self.plates_num_value = self._spinner_box(2, 100, 10)
        grid.addWidget(self.plates_num_value, 4, 1, 1, 2)

        buttons_row = 5
        cancel = QPushButton('Cancel')
        generate = QPushButton('Generate')
        grid.addWidget(cancel, buttons_row, 1, 1, 1)
        grid.addWidget(generate, buttons_row, 2, 1, 1)
        cancel.clicked.connect(self._on_cancel)
        generate.clicked.connect(self._on_generate)

        self.setLayout(grid)

    @staticmethod
    def _spinner_box(min_value, max_value, value):
        spinner = QSpinBox()
        spinner.setMinimum(min_value)
        spinner.setMaximum(max_value)
        spinner.setValue(value)
        return spinner

    def _on_cancel(self):
        QDialog.reject(self)

    def _on_generate(self):
        QDialog.accept(self)

    def seed(self):
        return self.seed_value.value()

    def width(self):
        return self.width_value.value()

    def height(self):
        return self.height_value.value()

    def num_plates(self):
        return self.plates_num_value.value()

    def name(self):
        return self.name_value.text()


class GenerationProgressDialog(QDialog):
    def __init__(self, parent, seed, name, width, height, num_plates):
        QDialog.__init__(self, parent)
        self._init_ui()
        self.world = None
        self.gen_thread = GenerationThread(self, seed, name, width, height,
                                           num_plates)
        self.gen_thread.start()

    def _init_ui(self):
        self.resize(400, 100)
        self.setWindowTitle('Generating a new world...')
        grid = QGridLayout()

        self.status = QLabel('....')
        grid.addWidget(self.status, 0, 0, 1, 3)

        cancel = QPushButton('Cancel')
        grid.addWidget(cancel, 1, 0, 1, 1)
        cancel.clicked.connect(self._on_cancel)

        done = QPushButton('Done')
        grid.addWidget(done, 1, 2, 1, 1)
        done.clicked.connect(self._on_done)
        done.setEnabled(False)
        self.done = done

        self.setLayout(grid)

    def _on_cancel(self):
        QDialog.reject(self)

    def _on_done(self):
        QDialog.accept(self)

    def on_finish(self):
        self.done.setEnabled(True)

    def set_status(self, message):
        self.status.setText(message)


class GenerationThread(threading.Thread):
    def __init__(self, ui, seed, name, width, height, num_plates):
        threading.Thread.__init__(self)
        self.plates_generation = PlatesGeneration(seed, name, width, height,
                                                  num_plates=num_plates)
        self.ui = ui

    def run(self):
        # FIXME it should be merged with world_gen
        finished = False
        while not finished:
            (finished, n_steps) = self.plates_generation.step()
            self.ui.set_status('Plate simulation: step %i' % n_steps)
        self.ui.set_status('Plate simulation: terminating plates simulation')
        w = self.plates_generation.world()
        self.ui.set_status('Plate simulation: center land')
        center_land(w)
        self.ui.set_status('Plate simulation: adding noise')
        add_noise_to_elevation(w, random.randint(0, 4096))
        self.ui.set_status('Plate simulation: forcing oceans at borders')
        place_oceans_at_map_borders(w)
        self.ui.set_status('Plate simulation: finalization (can take a while)')
        initialize_ocean_and_thresholds(w)
        self.ui.set_status('Plate simulation: completed')
        self.ui.world = w
        self.ui.on_finish()


class PlatesGeneration(object):
    def __init__(self, seed, name, width, height,
                 sea_level=0.65, erosion_period=60,
                 folding_ratio=0.02, aggr_overlap_abs=1000000,
                 aggr_overlap_rel=0.33,
                 cycle_count=2, num_plates=10):
        self.name = name
        self.width = width
        self.height = height
        self.seed = seed
        self.n_plates = num_plates
        self.ocean_level = sea_level
        self.p = platec.create(seed, width, height, sea_level, erosion_period,
                               folding_ratio,
                               aggr_overlap_abs, aggr_overlap_rel, cycle_count,
                               num_plates)
        self.steps = 0

    def step(self):
        if platec.is_finished(self.p) == 0:
            platec.step(self.p)
            self.steps += 1
            return False, self.steps
        else:
            return True, self.steps

    def world(self):
        world = World(self.name, self.width, self.height, self.seed,
                      self.n_plates, self.ocean_level,
                      Step.get_by_name("plates"))
        hm = platec.get_heightmap(self.p)
        pm = platec.get_platesmap(self.p)
        world.set_elevation(array_to_matrix(hm, self.width, self.height), None)
        world.set_plates(array_to_matrix(pm, self.width, self.height))
        return world


class MapCanvas(QImage):
    def __init__(self, label, width, height):
        QImage.__init__(self, width, height, QImage.Format_RGB32)
        self.label = label
        self._update()

    def draw_world(self, world, view):
        self.label.resize(world.width, world.height)
        if view == 'bw':
            draw_bw_elevation_on_screen(world, self)
        elif view == 'plates':
            draw_plates_on_screen(world, self)
        elif view == 'plates and elevation':
            draw_plates_and_elevation_on_screen(world, self)
        elif view == 'land':
            draw_land_on_screen(world, self)
        elif view == 'precipitations':
            PrecipitationsView().draw(world, self)
        elif view == 'watermap':
            WatermapView().draw(world, self)
        else:
            raise Exception("Unknown view %s" % view)
        self._update()

    def _update(self):
        self.label.setPixmap(QPixmap.fromImage(self))


class OperationDialog(QDialog):
    def __init__(self, parent, world, operation):
        QDialog.__init__(self, parent)
        self.operation = operation
        self._init_ui()
        self.op_thread = OperationThread(world, operation, self)
        self.op_thread.start()

    def _init_ui(self):
        self.resize(400, 100)
        self.setWindowTitle(self.operation.title())
        grid = QGridLayout()

        self.status = QLabel('....')
        grid.addWidget(self.status, 0, 0, 1, 3)

        cancel = QPushButton('Cancel')
        grid.addWidget(cancel, 1, 0, 1, 1)
        cancel.clicked.connect(self._on_cancel)

        done = QPushButton('Done')
        grid.addWidget(done, 1, 2, 1, 1)
        done.clicked.connect(self._on_done)
        done.setEnabled(False)
        self.done = done

        self.setLayout(grid)

    def _on_cancel(self):
        QDialog.reject(self)

    def _on_done(self):
        QDialog.accept(self)

    def on_finish(self):
        self.done.setEnabled(True)

    def set_status(self, message):
        self.status.setText(message)


class OperationThread(threading.Thread):
    def __init__(self, world, operation, ui):
        threading.Thread.__init__(self)
        self.world = world
        self.operation = operation
        self.ui = ui

    def run(self):
        self.operation.execute(self.world, self.ui)


class SimulationOp(object):
    def __init__(self, title, simulation):
        self._title = title
        self.simulation = simulation

    def title(self):
        return self._title

    def execute(self, world, ui):
        """

        :param ui: the dialog with the set_status and on_finish methods
        :return:
        """
        seed = random.randint(0, 65536)
        ui.set_status("%s: started (seed %i)" % (self.title(), seed))
        self.simulation.execute(world, seed)
        ui.set_status("%s: done (seed %i)" % (self.title(), seed))
        ui.on_finish()


class WorldEngineGui(QMainWindow):
    def __init__(self):
        super(WorldEngineGui, self).__init__()
        self._init_ui()
        self.world = None
        self.current_view = None
        self.canvas = None

    def set_status(self, message):
        self.statusBar().showMessage(message)

    def _init_ui(self):
        self.resize(800, 600)
        self.setWindowTitle('Worldengine - A world generator')
        self.set_status('No world selected: create or load a world')
        self._prepare_menu()
        self.label = QLabel()
        self.canvas = MapCanvas(self.label, 0, 0)

        # dummy widget to contain the layout manager
        self.main_widget = QWidget(self)

        self.setCentralWidget(self.main_widget)
        self.layout = QGridLayout(self.main_widget)
        # Set the stretch
        self.layout.setColumnStretch(0, 1)
        self.layout.setColumnStretch(2, 1)
        self.layout.setRowStretch(0, 1)
        self.layout.setRowStretch(2, 1)
        # Add widgets
        self.layout.addWidget(self.label, 1, 1)
        self.show()

    def set_world(self, world):
        self.world = world
        self.canvas = MapCanvas(self.label, self.world.width,
                                self.world.height)
        self._on_bw_view()

        self.saveproto_action.setEnabled(world is not None)
        self.bw_view.setEnabled(world is not None)
        self.plates_view.setEnabled(world is not None)
        self.plates_bw_view.setEnabled(world is not None)
        self.watermap_view.setEnabled(
            world is not None and WatermapView().is_applicable(world))
        self.precipitations_view.setEnabled(
            world is not None and PrecipitationsView().is_applicable(world))
        self.land_and_ocean_view.setEnabled(world is not None)

        self.precipitations_action.setEnabled(
            world is not None and (not world.has_precipitations()))
        self.watermap_action.setEnabled(
            world is not None and WatermapSimulation().is_applicable(world))
        self.irrigation_action.setEnabled(
            world is not None and IrrigationSimulation().is_applicable(world))
        self.humidity_action.setEnabled(
            world is not None and HumiditySimulation().is_applicable(world))
        self.temperature_action.setEnabled(
            world is not None and TemperatureSimulation().is_applicable(world))
        self.permeability_action.setEnabled(
            world is not None and
            PermeabilitySimulation().is_applicable(world))
        self.biome_action.setEnabled(
            world is not None and BiomeSimulation().is_applicable(world))
        self.erosion_action.setEnabled(
            world is not None and ErosionSimulation().is_applicable(world))

    def _prepare_menu(self):
        generate_action = QAction('&Generate', self)
        generate_action.setShortcut('Ctrl+G')
        generate_action.setStatusTip('Generate new world')
        generate_action.triggered.connect(self._on_generate)

        exit_action = QAction('Leave', self)
        exit_action.setShortcut('Ctrl+L')
        exit_action.setStatusTip('Exit application')
        exit_action.triggered.connect(QApplication.quit)

        open_action = QAction('&Open', self)
        open_action.triggered.connect(self._on_open)

        self.saveproto_action = QAction('&Save (protobuf)', self)
        self.saveproto_action.setEnabled(False)
        self.saveproto_action.setShortcut('Ctrl+S')
        self.saveproto_action.setStatusTip('Save (protobuf format)')
        self.saveproto_action.triggered.connect(self._on_save_protobuf)

        self.bw_view = QAction('Black and white', self)
        self.bw_view.triggered.connect(self._on_bw_view)
        self.plates_view = QAction('Plates', self)
        self.plates_view.triggered.connect(self._on_plates_view)
        self.plates_bw_view = QAction('Plates and elevation', self)
        self.plates_bw_view.triggered.connect(
            self._on_plates_and_elevation_view)
        self.land_and_ocean_view = QAction('Land and ocean', self)
        self.land_and_ocean_view.triggered.connect(self._on_land_view)
        self.precipitations_view = QAction('Precipitations', self)
        self.precipitations_view.triggered.connect(
            self._on_precipitations_view)
        self.watermap_view = QAction('Watermap', self)
        self.watermap_view.triggered.connect(self._on_watermap_view)

        self.bw_view.setEnabled(False)
        self.plates_view.setEnabled(False)
        self.plates_bw_view.setEnabled(False)
        self.land_and_ocean_view.setEnabled(False)
        self.precipitations_view.setEnabled(False)
        self.watermap_view.setEnabled(False)

        self.precipitations_action = QAction('Precipitations', self)
        self.precipitations_action.triggered.connect(self._on_precipitations)
        self.precipitations_action.setEnabled(False)

        self.erosion_action = QAction('Erosion', self)
        self.erosion_action.triggered.connect(self._on_erosion)
        self.erosion_action.setEnabled(False)

        self.watermap_action = QAction('Watermap', self)
        self.watermap_action.triggered.connect(self._on_watermap)
        self.watermap_action.setEnabled(False)

        self.irrigation_action = QAction('Irrigation', self)
        self.irrigation_action.triggered.connect(self._on_irrigation)
        self.irrigation_action.setEnabled(False)

        self.humidity_action = QAction('Humidity', self)
        self.humidity_action.triggered.connect(self._on_humidity)
        self.humidity_action.setEnabled(False)

        self.temperature_action = QAction('Temperature', self)
        self.temperature_action.triggered.connect(self._on_temperature)
        self.temperature_action.setEnabled(False)

        self.permeability_action = QAction('Permeability', self)
        self.permeability_action.triggered.connect(self._on_permeability)
        self.permeability_action.setEnabled(False)

        self.biome_action = QAction('Biome', self)
        self.biome_action.triggered.connect(self._on_biome)
        self.biome_action.setEnabled(False)

        menubar = self.menuBar()

        file_menu = menubar.addMenu('&File')
        file_menu.addAction(generate_action)
        file_menu.addAction(open_action)
        file_menu.addAction(self.saveproto_action)
        file_menu.addAction(exit_action)

        simulations_menu = menubar.addMenu('&Simulations')
        simulations_menu.addAction(self.precipitations_action)
        simulations_menu.addAction(self.erosion_action)
        simulations_menu.addAction(self.watermap_action)
        simulations_menu.addAction(self.irrigation_action)
        simulations_menu.addAction(self.humidity_action)
        simulations_menu.addAction(self.temperature_action)
        simulations_menu.addAction(self.permeability_action)
        simulations_menu.addAction(self.biome_action)

        view_menu = menubar.addMenu('&View')
        view_menu.addAction(self.bw_view)
        view_menu.addAction(self.plates_view)
        view_menu.addAction(self.plates_bw_view)
        view_menu.addAction(self.land_and_ocean_view)
        view_menu.addAction(self.precipitations_view)
        view_menu.addAction(self.watermap_view)

    def _on_bw_view(self):
        self.current_view = 'bw'
        self.canvas.draw_world(self.world, self.current_view)

    def _on_plates_view(self):
        self.current_view = 'plates'
        self.canvas.draw_world(self.world, self.current_view)

    def _on_plates_and_elevation_view(self):
        self.current_view = 'plates and elevation'
        self.canvas.draw_world(self.world, self.current_view)

    def _on_land_view(self):
        self.current_view = 'land'
        self.canvas.draw_world(self.world, self.current_view)

    def _on_precipitations_view(self):
        self.current_view = 'precipitations'
        self.canvas.draw_world(self.world, self.current_view)

    def _on_watermap_view(self):
        self.current_view = 'watermap'
        self.canvas.draw_world(self.world, self.current_view)

    def _on_generate(self):
        dialog = GenerateDialog(self)
        ok = dialog.exec_()
        if ok:
            seed = dialog.seed()
            width = dialog.width()
            height = dialog.height()
            num_plates = dialog.num_plates()
            name = str(dialog.name())
            dialog2 = GenerationProgressDialog(self, seed, name, width, height,
                                               num_plates)
            ok2 = dialog2.exec_()
            if ok2:
                self.set_world(dialog2.world)

    def _on_save_protobuf(self):
        filename = QFileDialog.getSaveFileName(self, "Save world", "",
                                                     "*.world")
        self.world.protobuf_to_file(filename)

    def _on_open(self):
        filename = QFileDialog.getOpenFileName(self, "Open world", "",
                                                     "*.world")
        world = World.open_protobuf(filename)
        self.set_world(world)

    def _on_precipitations(self):
        dialog = OperationDialog(self, self.world,
                                 SimulationOp("Simulating precipitations",
                                              PrecipitationSimulation()))
        ok = dialog.exec_()
        if ok:
            # just to refresh things to enable
            self.set_world(self.world)

    def _on_erosion(self):
        dialog = OperationDialog(self, self.world,
                                 SimulationOp("Simulating erosion",
                                              ErosionSimulation()))
        ok = dialog.exec_()
        if ok:
            # just to refresh things to enable
            self.set_world(self.world)

    def _on_watermap(self):
        dialog = OperationDialog(self, self.world,
                                 SimulationOp("Simulating water flow",
                                              WatermapSimulation()))
        ok = dialog.exec_()
        if ok:
            # just to refresh things to enable
            self.set_world(self.world)

    def _on_irrigation(self):
        dialog = OperationDialog(self, self.world,
                                 SimulationOp("Simulating irrigation",
                                              IrrigationSimulation()))
        ok = dialog.exec_()
        if ok:
            # just to refresh things to enable
            self.set_world(self.world)

    def _on_humidity(self):
        dialog = OperationDialog(self, self.world,
                                 SimulationOp("Simulating humidity",
                                              HumiditySimulation()))
        ok = dialog.exec_()
        if ok:
            # just to refresh things to enable
            self.set_world(self.world)

    def _on_temperature(self):
        dialog = OperationDialog(self, self.world,
                                 SimulationOp("Simulating temperature",
                                              TemperatureSimulation()))
        ok = dialog.exec_()
        if ok:
            # just to refresh things to enable
            self.set_world(self.world)

    def _on_permeability(self):
        dialog = OperationDialog(self, self.world,
                                 SimulationOp("Simulating permeability",
                                              PermeabilitySimulation()))
        ok = dialog.exec_()
        if ok:
            # just to refresh things to enable
            self.set_world(self.world)

    def _on_biome(self):
        dialog = OperationDialog(self, self.world,
                                 SimulationOp("Simulating biome",
                                              BiomeSimulation()))
        ok = dialog.exec_()
        if ok:
            # just to refresh things to enable
            self.set_world(self.world)


app = QApplication(sys.argv)
lg = WorldEngineGui()
assert lg
sys.exit(app.exec_())
