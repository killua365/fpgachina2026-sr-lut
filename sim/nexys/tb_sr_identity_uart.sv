`timescale 1ns/1ps
module tb_sr_identity_uart;
    reg clk=0;always #5 clk=~clk;
    reg [2943:0] payload=0;
    wire tx;
    bridge_uart #(.CLOCK_HZ(1000000),.BAUD(100000),.INTERVAL(40000),.BUSINESS_IDENTITY(1)) dut(.*);
    integer i,k,frame,fd;
    reg [7:0] data;
    initial begin
        for(i=0;i<368;i=i+1) payload[i*8 +: 8]=i;
        fd=$fopen("uart.hex","w");
        for(frame=0;frame<2;frame=frame+1) begin
            for(i=0;i<381;i=i+1) begin
                @(negedge tx);#150;
                for(k=0;k<8;k=k+1) begin data[k]=tx;#100;end
                if(tx!==1'b1) $fatal(1,"UART stop bit");
                $fwrite(fd,"%02x",data);
                if(frame==0 && i==0) payload={2944{1'b1}};
            end
            $fwrite(fd,"\n");
        end
        $fclose(fd);$display("PASS: UART 2 complete frames");$finish;
    end
    initial begin #1600000;$fatal(1,"UART bench timeout");end
endmodule
