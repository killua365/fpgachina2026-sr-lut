"""Strict readback identity parser; never accesses a device."""
import re
def properties(path, expected_usr):
    # Vivado uses the Windows locale for paths in this report. Identity fields
    # are ASCII: match those bytes without decoding unrelated path metadata.
    content = path.read_bytes()
    expected = {'IDCODE_HEX': '13636093', 'REGISTER.USR_ACCESS': expected_usr,
                'REGISTER.CONFIG_STATUS.BIT00_CRC_ERROR': '0',
                'REGISTER.CONFIG_STATUS.BIT12_INIT_B_PIN': '1',
                'REGISTER.CONFIG_STATUS.BIT14_DONE_PIN': '1',
                'REGISTER.CONFIG_STATUS.BIT15_IDCODE_ERROR': '0',
                'REGISTER.CONFIG_STATUS.BIT08_MODE_PIN_M[0]': '1',
                'REGISTER.CONFIG_STATUS.BIT09_MODE_PIN_M[1]': '0',
                'REGISTER.CONFIG_STATUS.BIT10_MODE_PIN_M[2]': '0'}
    for name, value in expected.items():
        matches = re.findall(rb'^'+re.escape(name.encode('ascii'))+
                             rb'[ \t]+\S+[ \t]+\S+[ \t]+([^\s]+)[ \t]*\r?$', content, re.M)
        if matches != [value.encode('ascii')]:
            raise ValueError('Configuration readback mismatch: '+name)

