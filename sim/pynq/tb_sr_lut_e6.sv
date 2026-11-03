`timescale 1ns/1ps

module tb_sr_lut_e6;

  localparam int unsigned INPUT_BLOCKS = 144;
  localparam int unsigned FUSION_BLOCKS = 36;
  localparam int unsigned FLOW_LENGTH = 256;
  localparam int unsigned MAX_PRINTED_ERRORS = 24;

  localparam int unsigned X2_OUTPUTS = sr_lut_pkg::X2_OUTPUTS_PER_ENTRY;
  localparam int unsigned X4_OUTPUTS = sr_lut_pkg::X4_OUTPUTS_PER_ENTRY;
  localparam int unsigned NUMERATOR_BITS = sr_lut_pkg::NUMERATOR_BITS;
  localparam int unsigned FUSION_BITS = sr_lut_pkg::FUSION_BITS;
  localparam int unsigned PIXEL_BITS = sr_lut_pkg::PIXEL_BITS;

  logic clk;
  logic resetn;

  logic [31:0] x2_neighborhood_mem [0:INPUT_BLOCKS - 1];
  logic [(X2_OUTPUTS * NUMERATOR_BITS) - 1:0]
      x2_native_mem [0:INPUT_BLOCKS - 1];
  logic [(X2_OUTPUTS * NUMERATOR_BITS) - 1:0]
      x2_restored_mem [0:INPUT_BLOCKS - 1];
  logic [(X2_OUTPUTS * FUSION_BITS) - 1:0]
      x2_fused_mem [0:FUSION_BLOCKS - 1];
  logic [(X2_OUTPUTS * NUMERATOR_BITS) - 1:0]
      x2_rounded_mem [0:FUSION_BLOCKS - 1];

  logic [31:0] x4_neighborhood_mem [0:INPUT_BLOCKS - 1];
  logic [(X4_OUTPUTS * NUMERATOR_BITS) - 1:0]
      x4_native_mem [0:INPUT_BLOCKS - 1];
  logic [(X4_OUTPUTS * NUMERATOR_BITS) - 1:0]
      x4_restored_mem [0:INPUT_BLOCKS - 1];
  logic [(X4_OUTPUTS * FUSION_BITS) - 1:0]
      x4_fused_mem [0:FUSION_BLOCKS - 1];
  logic [(X4_OUTPUTS * NUMERATOR_BITS) - 1:0]
      x4_rounded_mem [0:FUSION_BLOCKS - 1];

  logic source_valid_pattern [0:FLOW_LENGTH - 1];
  logic sink_ready_pattern [0:FLOW_LENGTH - 1];

  logic [31:0] x2_source_data;
  logic x2_source_valid;
  logic x2_source_ready;
  logic x2_source_last;
  logic [(X2_OUTPUTS * NUMERATOR_BITS) - 1:0] x2_native;
  logic [(X2_OUTPUTS * NUMERATOR_BITS) - 1:0] x2_restored;
  logic [1:0] x2_native_direction;
  logic [1:0] x2_native_channel;
  logic x2_native_valid;
  logic x2_native_ready;
  logic x2_native_last;
  logic [(X2_OUTPUTS * FUSION_BITS) - 1:0] x2_fused;
  logic [(X2_OUTPUTS * NUMERATOR_BITS) - 1:0] x2_rounded;
  logic [(X2_OUTPUTS * PIXEL_BITS) - 1:0] x2_clipped;
  logic [1:0] x2_fused_channel;
  logic x2_fused_valid;
  logic x2_fused_ready;
  logic x2_fused_last;

  logic [31:0] x4_source_data;
  logic x4_source_valid;
  logic x4_source_ready;
  logic x4_source_last;
  logic [(X4_OUTPUTS * NUMERATOR_BITS) - 1:0] x4_native;
  logic [(X4_OUTPUTS * NUMERATOR_BITS) - 1:0] x4_restored;
  logic [1:0] x4_native_direction;
  logic [1:0] x4_native_channel;
  logic x4_native_valid;
  logic x4_native_ready;
  logic x4_native_last;
  logic [(X4_OUTPUTS * FUSION_BITS) - 1:0] x4_fused;
  logic [(X4_OUTPUTS * NUMERATOR_BITS) - 1:0] x4_rounded;
  logic [(X4_OUTPUTS * PIXEL_BITS) - 1:0] x4_clipped;
  logic [1:0] x4_fused_channel;
  logic x4_fused_valid;
  logic x4_fused_ready;
  logic x4_fused_last;

  integer error_count;
  integer scheduler_block_checks;
  integer rotation_block_checks;
  integer fusion_block_checks;
  integer scheduler_lane_checks;
  integer fusion_lane_checks;
  integer stall_checks;

  sr_lut_rgb_scheduler #(
    .SAMPLE_BITS(sr_lut_pkg::X2_SAMPLE_BITS),
    .Q(sr_lut_pkg::X2_Q),
    .LEVELS(sr_lut_pkg::X2_LEVELS),
    .LUT_DEPTH(sr_lut_pkg::X2_LUT_DEPTH),
    .ADDR_BITS(sr_lut_pkg::X2_ADDR_BITS),
    .OUTPUTS(X2_OUTPUTS),
    .CORE_COUNT(1),
    .MEM_FILE("data/lut/bram_mem/model_s_x2_4bit.mem")
  ) x2_scheduler (
    .clk_i(clk),
    .resetn_i(resetn),
    .s_neighborhood_i(x2_source_data),
    .s_valid_i(x2_source_valid),
    .s_ready_o(x2_source_ready),
    .s_last_i(x2_source_last),
    .m_native_o(x2_native),
    .m_direction_o(x2_native_direction),
    .m_channel_o(x2_native_channel),
    .m_valid_o(x2_native_valid),
    .m_ready_i(x2_native_ready),
    .m_last_o(x2_native_last)
  );

  sr_lut_rotation_map #(
    .SCALE(sr_lut_pkg::X2_SCALE)
  ) x2_rotation (
    .block_i(x2_native),
    .direction_i(x2_native_direction),
    .block_o(x2_restored)
  );

  sr_lut_fusion #(
    .SAMPLE_BITS(sr_lut_pkg::X2_SAMPLE_BITS),
    .Q(sr_lut_pkg::X2_Q),
    .OUTPUTS(X2_OUTPUTS)
  ) x2_fusion_unit (
    .clk_i(clk),
    .resetn_i(resetn),
    .s_restored_i(x2_restored),
    .s_direction_i(x2_native_direction),
    .s_channel_i(x2_native_channel),
    .s_valid_i(x2_native_valid),
    .s_ready_o(x2_native_ready),
    .s_last_i(x2_native_last),
    .m_fused_o(x2_fused),
    .m_rounded_o(x2_rounded),
    .m_clipped_o(x2_clipped),
    .m_channel_o(x2_fused_channel),
    .m_valid_o(x2_fused_valid),
    .m_ready_i(x2_fused_ready),
    .m_last_o(x2_fused_last)
  );

  sr_lut_rgb_scheduler #(
    .SAMPLE_BITS(sr_lut_pkg::X4_SAMPLE_BITS),
    .Q(sr_lut_pkg::X4_Q),
    .LEVELS(sr_lut_pkg::X4_LEVELS),
    .LUT_DEPTH(sr_lut_pkg::X4_LUT_DEPTH),
    .ADDR_BITS(sr_lut_pkg::X4_ADDR_BITS),
    .OUTPUTS(X4_OUTPUTS),
    .CORE_COUNT(3),
    .MEM_FILE("data/lut/bram_mem/model_s_x4_5bit.mem")
  ) x4_scheduler (
    .clk_i(clk),
    .resetn_i(resetn),
    .s_neighborhood_i(x4_source_data),
    .s_valid_i(x4_source_valid),
    .s_ready_o(x4_source_ready),
    .s_last_i(x4_source_last),
    .m_native_o(x4_native),
    .m_direction_o(x4_native_direction),
    .m_channel_o(x4_native_channel),
    .m_valid_o(x4_native_valid),
    .m_ready_i(x4_native_ready),
    .m_last_o(x4_native_last)
  );

  sr_lut_rotation_map #(
    .SCALE(sr_lut_pkg::X4_SCALE)
  ) x4_rotation (
    .block_i(x4_native),
    .direction_i(x4_native_direction),
    .block_o(x4_restored)
  );

  sr_lut_fusion #(
    .SAMPLE_BITS(sr_lut_pkg::X4_SAMPLE_BITS),
    .Q(sr_lut_pkg::X4_Q),
    .OUTPUTS(X4_OUTPUTS)
  ) x4_fusion_unit (
    .clk_i(clk),
    .resetn_i(resetn),
    .s_restored_i(x4_restored),
    .s_direction_i(x4_native_direction),
    .s_channel_i(x4_native_channel),
    .s_valid_i(x4_native_valid),
    .s_ready_o(x4_native_ready),
    .s_last_i(x4_native_last),
    .m_fused_o(x4_fused),
    .m_rounded_o(x4_rounded),
    .m_clipped_o(x4_clipped),
    .m_channel_o(x4_fused_channel),
    .m_valid_o(x4_fused_valid),
    .m_ready_i(x4_fused_ready),
    .m_last_o(x4_fused_last)
  );

  always #5 clk = ~clk;

  task automatic record_error(input string message);
    begin
      error_count = error_count + 1;
      if (error_count <= MAX_PRINTED_ERRORS) begin
        $display("ERROR %s", message);
      end
    end
  endtask

  task automatic reset_duts;
    begin
      x2_source_data = '0;
      x2_source_valid = 1'b0;
      x2_source_last = 1'b0;
      x2_fused_ready = 1'b0;
      x4_source_data = '0;
      x4_source_valid = 1'b0;
      x4_source_last = 1'b0;
      x4_fused_ready = 1'b0;
      resetn = 1'b0;
      repeat (3) @(posedge clk);
      #1;
      resetn = 1'b1;
    end
  endtask

  task automatic run_x2(input logic use_flow);
    integer input_index;
    integer native_index;
    integer fused_index;
    integer cycle_index;
    integer lane;
    integer rounded_lane;
    logic [1:0] expected_direction;
    logic [1:0] expected_channel;
    logic [(X2_OUTPUTS * PIXEL_BITS) - 1:0] expected_clipped;
    logic signed [FUSION_BITS - 1:0] expected_fused_lane;
    logic native_hold_valid;
    logic [(X2_OUTPUTS * NUMERATOR_BITS) - 1:0] native_hold_data;
    logic [1:0] native_hold_direction;
    logic [1:0] native_hold_channel;
    logic native_hold_last;
    logic fused_hold_valid;
    logic [(X2_OUTPUTS * FUSION_BITS) - 1:0] fused_hold_data;
    logic [(X2_OUTPUTS * NUMERATOR_BITS) - 1:0] rounded_hold_data;
    logic [(X2_OUTPUTS * PIXEL_BITS) - 1:0] clipped_hold_data;
    logic [1:0] fused_hold_channel;
    logic fused_hold_last;
    begin
      reset_duts();
      input_index = 0;
      native_index = 0;
      fused_index = 0;
      cycle_index = 0;
      native_hold_valid = 1'b0;
      fused_hold_valid = 1'b0;
      $display("E6 x2 pipeline flow=%0d", use_flow);

      while (input_index < INPUT_BLOCKS
             || native_index < INPUT_BLOCKS
             || fused_index < FUSION_BLOCKS) begin
        @(negedge clk);
        x2_fused_ready =
            !use_flow || sink_ready_pattern[cycle_index % FLOW_LENGTH];

        if (x2_native_valid && x2_native_ready) begin
          expected_direction = (native_index / 3) % 4;
          expected_channel = native_index % 3;
          if (x2_native !== x2_native_mem[native_index]) begin
            record_error($sformatf(
              "x2 flow=%0d native=%0d data mismatch",
              use_flow, native_index
            ));
          end
          if (x2_restored !== x2_restored_mem[native_index]) begin
            record_error($sformatf(
              "x2 flow=%0d restored=%0d data mismatch",
              use_flow, native_index
            ));
          end
          if (x2_native_direction !== expected_direction
              || x2_native_channel !== expected_channel) begin
            record_error($sformatf(
              "x2 flow=%0d native=%0d tag mismatch",
              use_flow, native_index
            ));
          end
          if (x2_native_last !== (native_index == INPUT_BLOCKS - 1)) begin
            record_error($sformatf(
              "x2 flow=%0d native=%0d last mismatch",
              use_flow, native_index
            ));
          end
          native_index = native_index + 1;
          scheduler_block_checks = scheduler_block_checks + 1;
          rotation_block_checks = rotation_block_checks + 1;
          scheduler_lane_checks = scheduler_lane_checks + X2_OUTPUTS;
          native_hold_valid = 1'b0;
        end else if (x2_native_valid) begin
          if (native_hold_valid
              && (x2_native !== native_hold_data
                  || x2_native_direction !== native_hold_direction
                  || x2_native_channel !== native_hold_channel
                  || x2_native_last !== native_hold_last)) begin
            record_error("x2 scheduler output changed during stall");
          end
          native_hold_valid = 1'b1;
          native_hold_data = x2_native;
          native_hold_direction = x2_native_direction;
          native_hold_channel = x2_native_channel;
          native_hold_last = x2_native_last;
          stall_checks = stall_checks + 1;
        end

        if (x2_fused_valid && x2_fused_ready) begin
          expected_clipped = '0;
          for (lane = 0; lane < X2_OUTPUTS; lane = lane + 1) begin
            expected_fused_lane = $signed(
              x2_fused_mem[fused_index][(lane * FUSION_BITS)
                                          +: FUSION_BITS]
            );
            if ($signed(x2_fused[(lane * FUSION_BITS) +: FUSION_BITS])
                !== expected_fused_lane) begin
              record_error($sformatf(
                "x2 flow=%0d fused=%0d lane=%0d mismatch",
                use_flow, fused_index, lane
              ));
            end
            rounded_lane = $signed(
              x2_rounded_mem[fused_index][(lane * NUMERATOR_BITS)
                                            +: NUMERATOR_BITS]
            );
            if (rounded_lane < 0) begin
              expected_clipped[(lane * PIXEL_BITS) +: PIXEL_BITS] = 0;
            end else if (rounded_lane > 255) begin
              expected_clipped[(lane * PIXEL_BITS) +: PIXEL_BITS] = 255;
            end else begin
              expected_clipped[(lane * PIXEL_BITS) +: PIXEL_BITS] =
                  rounded_lane[PIXEL_BITS - 1:0];
            end
          end
          if (x2_rounded !== x2_rounded_mem[fused_index]) begin
            record_error($sformatf(
              "x2 flow=%0d rounded=%0d data mismatch",
              use_flow, fused_index
            ));
          end
          if (x2_clipped !== expected_clipped) begin
            record_error($sformatf(
              "x2 flow=%0d clipped=%0d data mismatch",
              use_flow, fused_index
            ));
          end
          if (x2_fused_channel !== (fused_index % 3)) begin
            record_error($sformatf(
              "x2 flow=%0d fused=%0d channel mismatch",
              use_flow, fused_index
            ));
          end
          if (x2_fused_last !== (fused_index == FUSION_BLOCKS - 1)) begin
            record_error($sformatf(
              "x2 flow=%0d fused=%0d last mismatch",
              use_flow, fused_index
            ));
          end
          fused_index = fused_index + 1;
          fusion_block_checks = fusion_block_checks + 1;
          fusion_lane_checks = fusion_lane_checks + X2_OUTPUTS;
          fused_hold_valid = 1'b0;
        end else if (x2_fused_valid) begin
          if (fused_hold_valid
              && (x2_fused !== fused_hold_data
                  || x2_rounded !== rounded_hold_data
                  || x2_clipped !== clipped_hold_data
                  || x2_fused_channel !== fused_hold_channel
                  || x2_fused_last !== fused_hold_last)) begin
            record_error("x2 fusion output changed during stall");
          end
          fused_hold_valid = 1'b1;
          fused_hold_data = x2_fused;
          rounded_hold_data = x2_rounded;
          clipped_hold_data = x2_clipped;
          fused_hold_channel = x2_fused_channel;
          fused_hold_last = x2_fused_last;
          stall_checks = stall_checks + 1;
        end

        if (!(x2_source_valid && !x2_source_ready)) begin
          if (input_index < INPUT_BLOCKS
              && (!use_flow
                  || source_valid_pattern[cycle_index % FLOW_LENGTH])) begin
            x2_source_data = x2_neighborhood_mem[input_index];
            x2_source_valid = 1'b1;
            x2_source_last = (input_index == INPUT_BLOCKS - 1);
          end else begin
            x2_source_valid = 1'b0;
            x2_source_last = 1'b0;
          end
        end

        @(posedge clk);
        #1;
        if (x2_source_valid && x2_source_ready) begin
          input_index = input_index + 1;
        end
        cycle_index = cycle_index + 1;
        if (cycle_index > 30_000) begin
          $fatal(1, "x2 E6 test did not complete");
        end
      end
    end
  endtask

  task automatic run_x4(input logic use_flow);
    integer input_index;
    integer native_index;
    integer fused_index;
    integer cycle_index;
    integer lane;
    integer rounded_lane;
    logic [1:0] expected_direction;
    logic [1:0] expected_channel;
    logic [(X4_OUTPUTS * PIXEL_BITS) - 1:0] expected_clipped;
    logic signed [FUSION_BITS - 1:0] expected_fused_lane;
    logic native_hold_valid;
    logic [(X4_OUTPUTS * NUMERATOR_BITS) - 1:0] native_hold_data;
    logic [1:0] native_hold_direction;
    logic [1:0] native_hold_channel;
    logic native_hold_last;
    logic fused_hold_valid;
    logic [(X4_OUTPUTS * FUSION_BITS) - 1:0] fused_hold_data;
    logic [(X4_OUTPUTS * NUMERATOR_BITS) - 1:0] rounded_hold_data;
    logic [(X4_OUTPUTS * PIXEL_BITS) - 1:0] clipped_hold_data;
    logic [1:0] fused_hold_channel;
    logic fused_hold_last;
    begin
      reset_duts();
      input_index = 0;
      native_index = 0;
      fused_index = 0;
      cycle_index = 0;
      native_hold_valid = 1'b0;
      fused_hold_valid = 1'b0;
      $display("E6 x4 pipeline flow=%0d", use_flow);

      while (input_index < INPUT_BLOCKS
             || native_index < INPUT_BLOCKS
             || fused_index < FUSION_BLOCKS) begin
        @(negedge clk);
        x4_fused_ready =
            !use_flow || sink_ready_pattern[cycle_index % FLOW_LENGTH];

        if (x4_native_valid && x4_native_ready) begin
          expected_direction = (native_index / 3) % 4;
          expected_channel = native_index % 3;
          if (x4_native !== x4_native_mem[native_index]) begin
            record_error($sformatf(
              "x4 flow=%0d native=%0d data mismatch",
              use_flow, native_index
            ));
          end
          if (x4_restored !== x4_restored_mem[native_index]) begin
            record_error($sformatf(
              "x4 flow=%0d restored=%0d data mismatch",
              use_flow, native_index
            ));
          end
          if (x4_native_direction !== expected_direction
              || x4_native_channel !== expected_channel) begin
            record_error($sformatf(
              "x4 flow=%0d native=%0d tag mismatch",
              use_flow, native_index
            ));
          end
          if (x4_native_last !== (native_index == INPUT_BLOCKS - 1)) begin
            record_error($sformatf(
              "x4 flow=%0d native=%0d last mismatch",
              use_flow, native_index
            ));
          end
          native_index = native_index + 1;
          scheduler_block_checks = scheduler_block_checks + 1;
          rotation_block_checks = rotation_block_checks + 1;
          scheduler_lane_checks = scheduler_lane_checks + X4_OUTPUTS;
          native_hold_valid = 1'b0;
        end else if (x4_native_valid) begin
          if (native_hold_valid
              && (x4_native !== native_hold_data
                  || x4_native_direction !== native_hold_direction
                  || x4_native_channel !== native_hold_channel
                  || x4_native_last !== native_hold_last)) begin
            record_error("x4 scheduler output changed during stall");
          end
          native_hold_valid = 1'b1;
          native_hold_data = x4_native;
          native_hold_direction = x4_native_direction;
          native_hold_channel = x4_native_channel;
          native_hold_last = x4_native_last;
          stall_checks = stall_checks + 1;
        end

        if (x4_fused_valid && x4_fused_ready) begin
          expected_clipped = '0;
          for (lane = 0; lane < X4_OUTPUTS; lane = lane + 1) begin
            expected_fused_lane = $signed(
              x4_fused_mem[fused_index][(lane * FUSION_BITS)
                                          +: FUSION_BITS]
            );
            if ($signed(x4_fused[(lane * FUSION_BITS) +: FUSION_BITS])
                !== expected_fused_lane) begin
              record_error($sformatf(
                "x4 flow=%0d fused=%0d lane=%0d mismatch",
                use_flow, fused_index, lane
              ));
            end
            rounded_lane = $signed(
              x4_rounded_mem[fused_index][(lane * NUMERATOR_BITS)
                                            +: NUMERATOR_BITS]
            );
            if (rounded_lane < 0) begin
              expected_clipped[(lane * PIXEL_BITS) +: PIXEL_BITS] = 0;
            end else if (rounded_lane > 255) begin
              expected_clipped[(lane * PIXEL_BITS) +: PIXEL_BITS] = 255;
            end else begin
              expected_clipped[(lane * PIXEL_BITS) +: PIXEL_BITS] =
                  rounded_lane[PIXEL_BITS - 1:0];
            end
          end
          if (x4_rounded !== x4_rounded_mem[fused_index]) begin
            record_error($sformatf(
              "x4 flow=%0d rounded=%0d data mismatch",
              use_flow, fused_index
            ));
          end
          if (x4_clipped !== expected_clipped) begin
            record_error($sformatf(
              "x4 flow=%0d clipped=%0d data mismatch",
              use_flow, fused_index
            ));
          end
          if (x4_fused_channel !== (fused_index % 3)) begin
            record_error($sformatf(
              "x4 flow=%0d fused=%0d channel mismatch",
              use_flow, fused_index
            ));
          end
          if (x4_fused_last !== (fused_index == FUSION_BLOCKS - 1)) begin
            record_error($sformatf(
              "x4 flow=%0d fused=%0d last mismatch",
              use_flow, fused_index
            ));
          end
          fused_index = fused_index + 1;
          fusion_block_checks = fusion_block_checks + 1;
          fusion_lane_checks = fusion_lane_checks + X4_OUTPUTS;
          fused_hold_valid = 1'b0;
        end else if (x4_fused_valid) begin
          if (fused_hold_valid
              && (x4_fused !== fused_hold_data
                  || x4_rounded !== rounded_hold_data
                  || x4_clipped !== clipped_hold_data
                  || x4_fused_channel !== fused_hold_channel
                  || x4_fused_last !== fused_hold_last)) begin
            record_error("x4 fusion output changed during stall");
          end
          fused_hold_valid = 1'b1;
          fused_hold_data = x4_fused;
          rounded_hold_data = x4_rounded;
          clipped_hold_data = x4_clipped;
          fused_hold_channel = x4_fused_channel;
          fused_hold_last = x4_fused_last;
          stall_checks = stall_checks + 1;
        end

        if (!(x4_source_valid && !x4_source_ready)) begin
          if (input_index < INPUT_BLOCKS
              && (!use_flow
                  || source_valid_pattern[cycle_index % FLOW_LENGTH])) begin
            x4_source_data = x4_neighborhood_mem[input_index];
            x4_source_valid = 1'b1;
            x4_source_last = (input_index == INPUT_BLOCKS - 1);
          end else begin
            x4_source_valid = 1'b0;
            x4_source_last = 1'b0;
          end
        end

        @(posedge clk);
        #1;
        if (x4_source_valid && x4_source_ready) begin
          input_index = input_index + 1;
        end
        cycle_index = cycle_index + 1;
        if (cycle_index > 30_000) begin
          $fatal(1, "x4 E6 test did not complete");
        end
      end
    end
  endtask

  initial begin
    $readmemh(
      "data/golden/image_stream/x2_4bit/small_neighborhoods.mem",
      x2_neighborhood_mem
    );
    $readmemh(
      "data/golden/image_stream/x2_4bit/small_native_numerators.mem",
      x2_native_mem
    );
    $readmemh(
      "data/golden/image_stream/x2_4bit/small_restored_numerators.mem",
      x2_restored_mem
    );
    $readmemh(
      "data/golden/image_stream/x2_4bit/small_fused_numerators.mem",
      x2_fused_mem
    );
    $readmemh(
      "data/golden/image_stream/x2_4bit/small_rounded.mem",
      x2_rounded_mem
    );
    $readmemh(
      "data/golden/image_stream/x4_5bit/small_neighborhoods.mem",
      x4_neighborhood_mem
    );
    $readmemh(
      "data/golden/image_stream/x4_5bit/small_native_numerators.mem",
      x4_native_mem
    );
    $readmemh(
      "data/golden/image_stream/x4_5bit/small_restored_numerators.mem",
      x4_restored_mem
    );
    $readmemh(
      "data/golden/image_stream/x4_5bit/small_fused_numerators.mem",
      x4_fused_mem
    );
    $readmemh(
      "data/golden/image_stream/x4_5bit/small_rounded.mem",
      x4_rounded_mem
    );
    $readmemh(
      "data/golden/image_stream/flow_control/source_valid.mem",
      source_valid_pattern
    );
    $readmemh(
      "data/golden/image_stream/flow_control/sink_ready.mem",
      sink_ready_pattern
    );

    clk = 1'b0;
    resetn = 1'b0;
    x2_source_data = '0;
    x2_source_valid = 1'b0;
    x2_source_last = 1'b0;
    x2_fused_ready = 1'b0;
    x4_source_data = '0;
    x4_source_valid = 1'b0;
    x4_source_last = 1'b0;
    x4_fused_ready = 1'b0;
    error_count = 0;
    scheduler_block_checks = 0;
    rotation_block_checks = 0;
    fusion_block_checks = 0;
    scheduler_lane_checks = 0;
    fusion_lane_checks = 0;
    stall_checks = 0;

    run_x2(1'b0);
    run_x2(1'b1);
    run_x4(1'b0);
    run_x4(1'b1);

    if (error_count != 0) begin
      $fatal(
        1,
        "E6_PIPELINE_FAIL errors=%0d scheduler_blocks=%0d rotation_blocks=%0d fusion_blocks=%0d",
        error_count,
        scheduler_block_checks,
        rotation_block_checks,
        fusion_block_checks
      );
    end

    $display(
      "E6_PIPELINE_PASS configurations=2 flow_modes=2 scheduler_blocks=%0d rotation_blocks=%0d fusion_blocks=%0d scheduler_lanes=%0d fusion_lanes=%0d stalls=%0d",
      scheduler_block_checks,
      rotation_block_checks,
      fusion_block_checks,
      scheduler_lane_checks,
      fusion_lane_checks,
      stall_checks
    );
    $finish;
  end

endmodule
