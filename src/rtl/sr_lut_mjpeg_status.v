`timescale 1ns/1ps

module sr_lut_mjpeg_status (
  input wire frame_geometry_error,
  input wire encoder_overflow,
  input wire packer_overflow,
  input wire [31:0] jpeg_byte_count,
  output wire [31:0] status
);

  assign status = {jpeg_byte_count[28:0], packer_overflow, encoder_overflow,
                   frame_geometry_error};

endmodule
