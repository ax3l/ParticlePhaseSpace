from abc import ABC, abstractmethod
import pandas as pd
import topas2numpy as tp
import numpy as np
from pathlib import Path
from .utilities import get_rest_masses_from_pdg_codes
import ParticlePhaseSpace.__phase_space_config__ as ps_cfg
import ParticlePhaseSpace.__particle_config__ as particle_cfg
from ParticlePhaseSpace import UnitSet
import warnings
from ParticlePhaseSpace import ParticlePhaseSpaceUnits

units=ParticlePhaseSpaceUnits()

class _DataLoadersBase(ABC):
    """
    DataLoader Abstract Base Class.
    Inherited by new instances of DataLoaders
    """

    def __init__(self, input_data, particle_type=None, units=units('mm_MeV')):
        self.data = pd.DataFrame()
        if not isinstance(units, UnitSet):
            raise TypeError('units must be an instance of articlePhaseSpace.__unit_config__._UnitSet.'
                            'UnitSets are accessed through the ParticlePhaseSpaceUnits class')
        self._units = units
        self._columns = ps_cfg.get_all_column_names(self._units)
        self._energy_consistency_check_cutoff = .001 * self._units.energy.conversion # in cases where it is possible to check energy/momentum consistency,
        # discrepencies greater than this will raise an error


        if particle_type:
            if not isinstance(particle_type, str):
                allowed_particles = [el for el in list(particle_cfg.particle_properties.keys()) if isinstance(el, str)]
                try:
                    particle_type = particle_cfg.particle_properties[particle_type]['name']
                except KeyError:
                    raise Exception(f'unknown particle type: {particle_type}.'
                                    f'allowed particles are {allowed_particles}')
                if not particle_type in allowed_particles:
                    raise Exception(f'unknown particle type: {particle_type}.'
                                f'allowed particles are {allowed_particles}')
        self._particle_type = particle_type

        self._input_data = input_data
        self._check_input_data()
        self._import_data()
        self._check_loaded_data()

    @abstractmethod
    def _import_data(self):
        """
        this function loads the data into the PS object
        :return:
        """
        pass

    @ abstractmethod
    def _check_input_data(self):
        """
        check that the data is what you think it is (read in specific)
        :return:
        """
        pass

    def _check_loaded_data(self):
        """
        check that the phase space data
        1. contains the required columns
        2. doesn't contain any non-allowed columns
        3. doesn't contain NaN
        4. "particle id" should be unique
        """
        # required columns present?
        required_columns = ps_cfg.get_required_column_names(self._units)
        for col_name in required_columns:
            if not col_name in self.data.columns:
                raise AttributeError(f'invalid data input; required column "{col_name}" is missing')

        # all columns allowed?
        for col_name in self.data.columns:
            if not col_name in required_columns:
                raise AttributeError(f'non allowed column "{col_name}" in data.')

        # are NaNs present?
        if self.data.isnull().values.any():
            NaN_cols = self.data.columns[self.data.isna().any()].tolist()
            raise AttributeError(f'input data may not contain NaNs; the following columns contain NaN:'
                                 f'\n{NaN_cols}')

        tot_mom = np.sqrt(self.data[self._columns['px']]**2 + self.data[self._columns['py']]**2 + self.data[self._columns['pz']]**2)
        if not np.min(tot_mom)>0:
            raise Exception('particles with zero absolute momentum make no sense')

        # is every particle ID unique?
        if not len(self.data[self._columns['particle id']].unique()) == len(self.data[self._columns['particle id']]):
            raise Exception('you have attempted to create a data set with non'
                                 'unique "particle id" fields, which is not allowed')

        #all pdg codes valid?
        get_rest_masses_from_pdg_codes(self.data['particle type [pdg_code]'])

    def _check_energy_consistency(self, Ek):
        """
        for data formats that specify kinetic energy, this can be called at the end
        of _import data to check that the momentums in self.data give rise to the same kinetic
        energy as specified in the input data

        :param Ek: existing value to check against
        :return:
        """
        if not hasattr(self,'_rest_masses'):
            self._rest_masses = get_rest_masses_from_pdg_codes(self.data['particle type [pdg_code]'])
        Totm = np.sqrt((self.data[self._columns['px']] ** 2 + self.data[self._columns['py']] ** 2 + self.data[self._columns['pz']] ** 2))
        self.TOT_E = np.sqrt(Totm ** 2 + self._rest_masses ** 2)
        Ek_internal = np.subtract(self.TOT_E, self._rest_masses)

        E_error = max(Ek - Ek_internal)
        if E_error > self._energy_consistency_check_cutoff:  # .01 MeV is an aribitrary cut off
            raise Exception('Energy check failed: read in of data may be incorrect')


class Load_TopasData(_DataLoadersBase):
    """
    DataLoader for `Topas <https://topas.readthedocs.io/en/latest/>`_ data.
    This data loader will read in both ascii and binary topas phase space (phsp) files.
    At present, we do not handle time or particle-id fields which may or may not be present in topas data.
    Behind the scenes, it relies on `topas2numpy <https://github.com/davidchall/topas2numpy>`_::

        from ParticlePhaseSpace import DataLoaders
        from ParticlePhaseSpace import PhaseSpace
        from pathlib import Path

        data_loc = Path(r'../tests/test_data/coll_PhaseSpace_xAng_0.00_yAng_0.00_angular_error_0.0.phsp')

        data = DataLoaders.Load_TopasData(data_loc)
        PS = PhaseSpace(data)
    """

    def _import_data(self):
        """
        Read in topas  data
        This has been extensively tested for data travelling the z direction, but not so much in the x and y directions.
        since topas uses the direction cosines to define directions, I would be cautious about these other cases
        """
        topas_phase_space = tp.read_ntuple(self._input_data)
        ParticleTypes = topas_phase_space['Particle Type (in PDG Format)']
        self.data[self._columns['particle type']] = ParticleTypes.astype(int)
        self.data[self._columns['x']] = topas_phase_space['Position X [cm]'] * 1e1
        self.data[self._columns['y']] = topas_phase_space['Position Y [cm]'] * 1e1
        self.data[self._columns['z']] = topas_phase_space['Position Z [cm]'] * 1e1
        self.data[self._columns['weight']] = topas_phase_space['Weight']
        self.data[self._columns['particle id']] = np.arange(len(self.data))  # may want to replace with track ID if available?
        self.data[self._columns['time']] = 0  # may want to replace with time feature if available?
        # figure out the momentums:
        ParticleDir = topas_phase_space['Flag to tell if Third Direction Cosine is Negative (1 means true)']
        DirCosineX = topas_phase_space['Direction Cosine X']
        DirCosineY = topas_phase_space['Direction Cosine Y']
        E = topas_phase_space['Energy [MeV]']
        self._rest_masses = get_rest_masses_from_pdg_codes(self.data['particle type [pdg_code]'])
        P = np.sqrt((E + self._rest_masses) ** 2 - self._rest_masses ** 2)
        self.data[self._columns['px']] = np.multiply(P, DirCosineX)
        self.data[self._columns['py']] = np.multiply(P, DirCosineY)
        temp = P ** 2 - self.data[self._columns['px']] ** 2 - self.data[self._columns['py']] ** 2
        _negative_temp_ind = temp < 0
        if any(_negative_temp_ind):
            # this should never happen, but does occur when pz is essentially 0. we will attempt to resolve it here.
            negative_locations = np.where(_negative_temp_ind)[0]
            n_negative_locations = np.count_nonzero(_negative_temp_ind)
            momentum_precision_factor = 1e-3
            for location in negative_locations:
                relative_difference = np.divide(np.sqrt(abs(temp[location])), P[location])
                if relative_difference < momentum_precision_factor:
                    temp[location] = 0
                else:
                    raise Exception(f'failed to calculate momentums from topas data. Possible solution is to increase'
                                    f'the value of momentum_precision_factor, currently set to {momentum_precision_factor: 1.2e}'
                                    f'and failed data has value {relative_difference: 1.2e}')
            warnings.warn(f'{n_negative_locations: d} entries returned invalid pz values and were set to zero.'
                          f'\nWe will now check that momentum and energy are consistent to within '
                          f'{self._energy_consistency_check_cutoff: 1.4f} {self._units.energy.label}')

        ParticleDir = [-1 if elem else 1 for elem in ParticleDir]
        self.data[self._columns['pz']] = np.multiply(np.sqrt(temp), ParticleDir)
        self._check_energy_consistency(Ek=E)

    def _check_input_data(self):
        """
        In this case, just check that the file exists.
        The rest of the checks are handles inside topas2nupy
        """
        if not Path(self._input_data).is_file():
            raise FileNotFoundError(f'input data file {self._import_data()} does not exist')
        if not Path(self._input_data).suffix == '.phsp':
            raise Exception('The topas data loader reads in files of extension *.phsp')
        if self._particle_type:
            warnings.warn('particle type is ignored in topas read in')


class Load_PandasData(_DataLoadersBase):
    """
    loads in pandas data of the format. This is used internally by ParticlePhaseSpace, and can also be used
    externally in cases where it is not desired to write a dedicated new data loader::

        from ParticlePhaseSpace import DataLoaders
        import pandas as pd

        demo_data = pd.DataFrame(
            {'x [mm]': [0, 1, 2],
             'y [mm]': [0, 1, 2],
             'z [mm]': [0, 1, 2],
             'px [MeV/c]': [0, 1, 2],
             'py [MeV/c]': [0, 1, 2],
             'pz [MeV/c]': [0, 1, 2],
             'particle type [pdg_code]': [11, 11, 11],
             'weight': [0, 1, 2],
             'particle id': [0, 1, 2],
             'time [ps]': [0, 1, 2]})

        data = DataLoaders.Load_PandasData(demo_data)
    """

    def _import_data(self):

        self.data = self._input_data
        # make sure column names match the input units

        column_names = ps_cfg.get_required_column_names(self._units)
        for existing_col in self.data.columns:
            if not existing_col in column_names:
                raise Exception(f'the column names in the input pandas data are not consistent with the defined unit set:'
                                f'{self._units.label}')

        #         Note that the format of the data is checked by the base class,
        #         so no additional checks are required here

    def _check_input_data(self):
        """
        is pandas instance
        """
        assert isinstance(self._input_data, pd.DataFrame)

        if self._particle_type:
            raise AttributeError('particle_type should not be specified for pandas import')


class Load_TibarayData(_DataLoadersBase):
    """
    Load ASCII data from tibaray of format
    `x y z rxy Bx By Bz G t m q nmacro rmacro ID`::

        data_loc = Path(r'../tests/test_data/tibaray_test.dat')
        data = DataLoaders.Load_TibarayData(data_loc, particle_type=11)
        PS = PhaseSpace(data)
    """

    def _check_input_data(self):
        if not Path(self._input_data).is_file():
            raise FileNotFoundError(f'input data file {self._import_data()} does not exist')
        if not self._particle_type:
            raise Exception('particle_type must be specified when readin tibaray data')
        with open(self._input_data) as f:
            first_line = f.readline()
            if not first_line == 'x y z rxy Bx By Bz G t m q nmacro rmacro ID \n':
                warnings.warn('first line of tibaray data does not look as expected, proceed with caution')

    def _import_data(self):
        Data = np.loadtxt(self._input_data, skiprows=1)
        self.data[self._columns['x']] = Data[:, 0] * 1e3  # mm to m
        self.data[self._columns['y']] = Data[:, 1] * 1e3
        self.data[self._columns['z']] = Data[:, 2] * 1e3
        Bx = Data[:, 4]
        By = Data[:, 5]
        Bz = Data[:, 6]
        Gamma = Data[:, 7]
        self.data[self._columns['time']] = Data[:, 8] * 1e9
        m = Data[:, 9]
        q = Data[:, 10]
        self.data[self._columns['weight']] = Data[:, 11]
        rmacro = Data[:, 12]
        self.data[self._columns['particle id']] = Data[:, 13]
        self.data[self._columns['particle type']] = particle_cfg.particle_properties[self._particle_type]['pdg_code']

        self.data[self._columns['px']] = np.multiply(Bx, Gamma) * particle_cfg.particle_properties[self._particle_type]['rest_mass']
        self.data[self._columns['py']] = np.multiply(By, Gamma) * particle_cfg.particle_properties[self._particle_type]['rest_mass']
        self.data[self._columns['pz']] = np.multiply(Bz, Gamma) * particle_cfg.particle_properties[self._particle_type]['rest_mass']


class Load_p2sat_txt(_DataLoadersBase):
    """
    Adapted from the `p2sat <https://github.com/lesnat/p2sat/blob/master/p2sat/datasets/_LoadPhaseSpace.py>`_
    'txt' loader; loads csv data of format
    `# weight          x (um)          y (um)          z (um)          px (MeV/c)      py (MeV/c)      pz (MeV/c)      t (fs)`
    Note that we use a hard coded seperator value ",".
    ::

        available_units = ParticlePhaseSpaceUnits()
        data_url = 'https://raw.githubusercontent.com/lesnat/p2sat/master/examples/ExamplePhaseSpace.csv'
        file_name = 'p2sat_txt_test.csv'
        request.urlretrieve(data_url, file_name)
        # read in
        ps_data = DataLoaders.Load_p2sat_txt(file_name, particle_type='electrons', units=available_units('p2_sat_UHI'))
        PS = PhaseSpace(ps_data)
    """
    def _check_input_data(self):
        if not Path(self._input_data).is_file():
            raise FileNotFoundError(f'input data file {self._import_data()} does not exist')
        if not self._particle_type:
            raise Exception('particle_type must be specified when readin p2sat_txt data')

    def _import_data(self):
        # Initialize data lists
        w = []
        x, y, z = [], [], []
        px, py, pz = [], [], []
        t = []

        # Open file
        with open(self._input_data, 'r') as f:
            # Loop over lines
            for line in f.readlines():
                # If current line is not a comment, save data
                if line[0] != "#":
                    data = line.split(",")
                    w.append(float(data[0]))
                    x.append(float(data[1]))
                    y.append(float(data[2]))
                    z.append(float(data[3]))
                    px.append(float(data[4]))
                    py.append(float(data[5]))
                    pz.append(float(data[6]))
                    t.append(float(data[7]))


        self.data[self._columns['x']] = x
        self.data[self._columns['y']] = y
        self.data[self._columns['z']] = z
        self.data[self._columns['time']] = t
        self.data[self._columns['weight']] = w

        self.data[self._columns['particle id']] = np.arange(self.data.shape[0])
        self.data[self._columns['particle type']] = particle_cfg.particle_properties[self._particle_type]['pdg_code']

        self.data[self._columns['px']] = px
        self.data[self._columns['py']] = py
        self.data[self._columns['pz']] = pz


class Load_varian_IAEA(_DataLoadersBase):
    """
    this loads a binary varian IAEA sent through the topas forums.
    The format appears extremely specific, so I doubt this will work for general data,
    but it may be a usefult template for reading this extremely annoying data format
    """

    def _check_input_data(self):
        if not Path(self._input_data).is_file():
            raise FileNotFoundError(f'input data file {self._import_data()} does not exist')
        if not Path(self._input_data).suffix == '.phsp':
            raise Exception('This data loader reads in files of extension *.phsp')
        if self._particle_type:
            warnings.warn('particle type is ignored in IAEA read in')

    def _import_data(self):
        dt = np.dtype([('Type', 'i1'),
                       ('Energy', 'f4'),
                       ('X position', 'f4'),
                       ('Y position', 'f4'),
                       ('Component of momentum direction in X', 'f4'),
                       ('Component of momentum direction in Y', 'f4')])

        '''
        note that varian have named the last two columns inconsistently as they wrote out their notes on a type writer
        '''

        data = np.fromfile(self._input_data, dtype=dt)
        '''
        at this point we have an array of tuples, each tuple containing [particle_type, Energy, X, Y, CosineX, CosineY]
        in addition, the header specifies that the following two constants:
                
            26.7       // Constant Z
            1.0000     // Constant Weight
            
        So our job now:
        - convert the tuplles into a workable format
        - convert varians weird types with pdg types
        - populate the data frame        
        '''

        pdg_types = self._varian_types_to_pdg(data['Type'])  # this contains 1,2,3 - I am going to guess this means photons, electrons, positrons

        self.data[self._columns['particle type']] = pdg_types.astype(int)
        # self.data[self._columns['particle type']] = pd.Series(pdg_types, dtype="category")
        self.data[self._columns['x']] = data['X position']
        self.data[self._columns['y']] = data['Y position']
        self.data[self._columns['z']] = pd.Series(26.7 * np.ones(self.data.shape[0]), dtype="category")
        self.data[self._columns['weight']] = pd.Series(1 * np.ones(self.data.shape[0]), dtype="category")
        self.data[self._columns['particle id']] = np.arange(
            len(self.data))  # may want to replace with track ID if available?
        self.data[self._columns['time']] = pd.Series(0  * np.ones(self.data.shape[0]), dtype="category")  # may want to replace with time feature if available?
        # figure out the momentums:
        DirCosineX = data['Component of momentum direction in X']
        DirCosineY = data['Component of momentum direction in Y']
        E = data['Energy']
        if E.min() < 0:
            warnings.warn('this data has negative energy in it, wtf does that even mean. forcing all energy to positive')
            E = np.abs(E)
        self._rest_masses = get_rest_masses_from_pdg_codes(self.data['particle type [pdg_code]'])
        P = np.sqrt((E + self._rest_masses) ** 2 - self._rest_masses ** 2)
        self.data[self._columns['px']] = pd.Series(np.multiply(P, DirCosineX), dtype=np.float32)
        self.data[self._columns['py']] = pd.Series(np.multiply(P, DirCosineY), dtype=np.float32)
        temp = P ** 2 - self.data[self._columns['px']] ** 2 - self.data[self._columns['py']] ** 2
        _negative_temp_ind = temp < 0
        if any(_negative_temp_ind):
            # this should never happen, but does occur when pz is essentially 0. we will attempt to resolve it here.
            negative_locations = np.where(_negative_temp_ind)[0]
            n_negative_locations = np.count_nonzero(_negative_temp_ind)
            momentum_precision_factor = 1e-3
            for location in negative_locations:
                relative_difference = np.divide(np.sqrt(abs(temp[location])), P[location])
                if relative_difference < momentum_precision_factor:
                    temp[location] = 0
                else:
                    raise Exception(f'failed to calculate momentums from topas data. Possible solution is to increase'
                                    f'the value of momentum_precision_factor, currently set to {momentum_precision_factor: 1.2e}'
                                    f'and failed data has value {relative_difference: 1.2e}')
            warnings.warn(f'{n_negative_locations: d} entries returned invalid pz values and were set to zero.'
                          f'\nWe will now check that momentum and energy are consistent to within '
                          f'{self._energy_consistency_check_cutoff: 1.4f} {self._units.energy.label}')

        self.data[self._columns['pz']] = pd.Series(np.sqrt(temp), dtype=np.float32)
        self._check_energy_consistency(Ek=E)

    def _varian_types_to_pdg(self, varian_types):
        """
        convert varian integer type code to pdg integer type code
        :param varian_types:
        :return:
        """
        pdg_types = np.zeros(varian_types.shape, dtype=np.int)
        pdg_types[varian_types==1] = 22
        pdg_types[varian_types == 2] = 11
        pdg_types[varian_types == 3] = -11

        return pdg_types